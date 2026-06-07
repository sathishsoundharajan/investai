import os
import asyncio
import time
from datetime import datetime, timedelta, date, time as dtime
from graphiti_core import Graphiti
from graphiti_core.llm_client.gemini_client import GeminiClient, LLMConfig
from graphiti_core.embedder.gemini import GeminiEmbedder, GeminiEmbedderConfig
from graphiti_core.cross_encoder.gemini_reranker_client import GeminiRerankerClient
from neo4j import GraphDatabase
from core.config_loader import load_config
from core.logger import get_logger
from memory.schemas import AgentAnalysisOutput
from memory.db_client import QuantDB

logger = get_logger("graph_client")

class TemporalMemory:
    def __init__(self):
        self.config = load_config()
        self._graphiti: Graphiti | None = None

    async def initialize(self):
        """Init Graphiti with Neo4j + Gemini. Health check first."""
        if not self.config.gemini_api_key:
            logger.warning("Gemini API key not set. Running without graph memory.")
            return

        try:
            # F2: Validate Neo4j is reachable before proceeding
            await self._wait_for_neo4j(timeout_sec=10)

            # F1: SEMAPHORE_LIMIT=2 to stay under 15 RPM
            os.environ["SEMAPHORE_LIMIT"] = str(self.config.graphiti_semaphore_limit)

            self._graphiti = Graphiti(
                uri=self.config.neo4j_uri,
                user=self.config.neo4j_user,
                password=self.config.neo4j_password,
                llm_client=GeminiClient(
                    config=LLMConfig(
                        api_key=self.config.gemini_api_key,
                        model=self.config.graph_llm_model,
                    )
                ),
                embedder=GeminiEmbedder(
                    config=GeminiEmbedderConfig(
                        api_key=self.config.gemini_api_key,
                        embedding_model=self.config.graph_embedder,
                    ),
                    batch_size=1
                ),
                cross_encoder=GeminiRerankerClient(
                    config=LLMConfig(
                        api_key=self.config.gemini_api_key,
                        model=self.config.graph_llm_model,
                    )
                ),
            )
            await self._graphiti.build_indices_and_constraints()
            logger.info("Graphiti initialized with Neo4j backend.")
        except Exception as e:
            logger.warning(f"Graphiti init failed: {e}. Running without memory.")
            self._graphiti = None

    async def _wait_for_neo4j(self, timeout_sec: int = 10):
        """Poll Neo4j Bolt endpoint until responsive or timeout."""
        start = time.time()
        while time.time() - start < timeout_sec:
            try:
                driver = GraphDatabase.driver(
                    self.config.neo4j_uri,
                    auth=(self.config.neo4j_user, self.config.neo4j_password),
                )
                driver.verify_connectivity()
                driver.close()
                return
            except Exception:
                await asyncio.sleep(2)
        raise ConnectionError(f"Neo4j not reachable at {self.config.neo4j_uri}")

    async def add_analysis(self, output: AgentAnalysisOutput, timestamp: datetime):
        """Ingest analysis as an episode. Graphiti makes internal LLM calls here."""
        if not self._graphiti:
            return
        episode_text = self._format_episode(output)
        await self._graphiti.add_episode(
            name=f"{output.ticker}_analysis_{timestamp.isoformat()}",
            episode_body=episode_text,
            source_description=f"Gemini analysis of {output.ticker}",
            reference_time=timestamp,
        )

    async def get_historical_context(self, ticker: str, days_back: int = 14) -> str:
        """Search graph for relevant risks/events. Returns prompt-injectable text."""
        if not self._graphiti:
            return "No historical context available (knowledge graph offline)."
            
        try:
            # 1. Semantic search for permanent/older relevance
            results = await self._graphiti.search(
                f"Recent risks, events, and guidance for {ticker}",
                num_results=3
            )
            context = self._format_context(results)
            
            # 2. Direct pull for recent 14 days (Layer 1 Time-Gate)
            recent = self._get_recent_episodes(ticker, days_back)
            
            if recent:
                context += f"\n\nRecent Exact Events (Last {days_back} Days):\n" + recent
                
            return context
        except Exception as e:
            logger.warning(f"Failed to search Graphiti for {ticker}: {e}")
            return "No prior context found for this ticker."

    def _get_recent_episodes(self, ticker: str, days_back: int) -> str:
        cutoff_str = (datetime.utcnow() - timedelta(days=days_back)).isoformat()
        try:
            driver = GraphDatabase.driver(
                self.config.neo4j_uri,
                auth=(self.config.neo4j_user, self.config.neo4j_password)
            )
            with driver.session() as session:
                res = session.run("""
                    MATCH (e:Episode)
                    WHERE e.name CONTAINS $ticker 
                      AND e.reference_time >= datetime($cutoff)
                    RETURN e.episode_body AS fact, e.reference_time AS created_at
                    ORDER BY e.reference_time DESC
                """, ticker=ticker, cutoff=cutoff_str)
                lines = []
                for record in res:
                    lines.append(f"- {record['fact']} (as of {record['created_at']})")
            driver.close()
            return "\n".join(lines)
        except Exception as e:
            logger.error(f"Failed to pull recent episodes for {ticker}: {e}")
            return ""

    async def roll_up_week(self, ticker: str, db: QuantDB, week_ending: date):
        """Compress 7 daily episodes into one weekly summary episode, then prune dailies."""
        if not self._graphiti:
            return
            
        rows = db.get_week_analyses(ticker, days_back=7)
        if len(rows) < 3:
            logger.info(f"Not enough data to roll up week for {ticker}")
            return
            
        decisions = [r.action for r in rows]
        dominant = max(set(decisions), key=decisions.count)
        
        rationales = " ".join([r.rationale for r in rows])
        
        summary_text = (
            f"Weekly summary for {ticker}, week ending {week_ending.isoformat()}: "
            f"Decision trend: {', '.join(decisions)} (dominant: {dominant}). "
            f"Key rationale points: {rationales[:1000]}..."
        )
        
        # Add summary node
        await self._graphiti.add_episode(
            name=f"{ticker}_weekly_{week_ending.isoformat()}",
            episode_body=summary_text,
            source_description=f"Weekly roll-up for {ticker}",
            reference_time=datetime.combine(week_ending, dtime.min),
        )
        
        # Prune daily nodes
        cutoff_str = (datetime.utcnow() - timedelta(days=7)).isoformat()
        try:
            driver = GraphDatabase.driver(
                self.config.neo4j_uri,
                auth=(self.config.neo4j_user, self.config.neo4j_password)
            )
            with driver.session() as session:
                session.run("""
                    MATCH (e:Episode)
                    WHERE e.name STARTS WITH $ticker + '_analysis_' 
                      AND e.reference_time >= datetime($cutoff)
                    DETACH DELETE e
                """, ticker=ticker, cutoff=cutoff_str)
            driver.close()
            logger.info(f"Successfully rolled up week for {ticker}")
        except Exception as e:
            logger.error(f"Failed to prune daily episodes for {ticker}: {e}")

    async def close(self):
        if self._graphiti:
            # Note: graphiti_core.close() exists depending on version. We'll close neo4j driver
            try:
                self._graphiti.client.close()
            except:
                pass

    def _format_episode(self, output: AgentAnalysisOutput) -> str:
        risks = "; ".join(r.risk_name for r in output.identified_risks[:5])
        text = (
            f"{output.ticker}: {output.decision}. "
            f"Tone: {output.management_tone}. "
            f"Rationale: {output.decision_rationale[:200]}. "
            f"Risks: {risks}"
        )
        return text[:600]

    def _format_context(self, results) -> str:
        """Format Graphiti search results into readable context for prompt."""
        if not results:
            return "No prior context found for this ticker."
        lines = []
        # Structure of results depends on Graphiti version, usually a list of Node/Edge objects
        for edge in results:
            # Extract text safely
            fact = getattr(edge, 'fact', None)
            if not fact:
                edge_str = str(edge)
                fact = edge_str[:200] + "..." if len(edge_str) > 200 else edge_str
                
            created = getattr(edge, 'created_at', 'unknown date')
            lines.append(f"- {fact} (as of {created})")
        return "\n".join(lines)
