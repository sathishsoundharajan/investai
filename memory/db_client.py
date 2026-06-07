import sqlite3
from pathlib import Path
from datetime import datetime
from typing import List, Dict, Optional
from .schemas import PortfolioPosition, PositionHistory, MacroMetric, AgentExecutionLog

MIGRATIONS = {
    1: """
    CREATE TABLE portfolio_positions (
        ticker VARCHAR(10) PRIMARY KEY,
        shares REAL NOT NULL,
        avg_cost REAL NOT NULL
    );
    """,
    2: """
    CREATE TABLE position_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        ticker VARCHAR(10) NOT NULL,
        shares REAL NOT NULL,
        avg_cost REAL NOT NULL
    );
    """,
    3: """
    CREATE TABLE xbrl_metrics (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker VARCHAR(10) NOT NULL,
        period VARCHAR(10) NOT NULL,
        revenue BIGINT,
        free_cash_flow BIGINT,
        total_debt BIGINT,
        operating_margin REAL,
        UNIQUE(ticker, period)
    );
    """,
    4: """
    CREATE INDEX idx_xbrl_ticker ON xbrl_metrics(ticker);
    """,
    5: """
    CREATE TABLE macro_metrics (
        indicator_id VARCHAR(20) PRIMARY KEY,
        value REAL NOT NULL,
        last_updated DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """,
    6: """
    CREATE TABLE agent_execution_logs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        run_id VARCHAR(36) NOT NULL,
        timestamp DATETIME DEFAULT CURRENT_TIMESTAMP,
        ticker VARCHAR(10) NOT NULL,
        action VARCHAR(20) CHECK(action IN ('BUY','HOLD','REDUCE','SELL')),
        rationale TEXT NOT NULL,
        gemini_tokens INTEGER NOT NULL,
        graphiti_tokens INTEGER NOT NULL,
        tavily_credits_used INTEGER DEFAULT 0
    );
    CREATE UNIQUE INDEX idx_agent_exec_daily ON agent_execution_logs(ticker, DATE(timestamp));
    """,
    7: """
    CREATE TABLE filing_summaries (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker VARCHAR(10) NOT NULL,
        accession_number VARCHAR(30) NOT NULL UNIQUE,
        form_type VARCHAR(10) NOT NULL,
        filing_date DATE NOT NULL,
        summary TEXT NOT NULL,
        processed_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    CREATE INDEX idx_filing_ticker ON filing_summaries(ticker);
    """,
    8: """
    CREATE TABLE historical_financials (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker VARCHAR(10) NOT NULL,
        period_end DATE NOT NULL,
        revenue BIGINT,
        net_income BIGINT,
        ebitda BIGINT,
        free_cash_flow BIGINT,
        total_debt BIGINT,
        total_assets BIGINT,
        UNIQUE(ticker, period_end)
    );
    CREATE TABLE forward_estimates (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        ticker VARCHAR(10) NOT NULL,
        period VARCHAR(10) NOT NULL,
        avg_estimate REAL,
        low_estimate REAL,
        high_estimate REAL,
        number_of_analysts INTEGER,
        fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP,
        UNIQUE(ticker, period)
    );
    """,
    9: """
    CREATE TABLE api_cache (
        cache_key VARCHAR(255) PRIMARY KEY,
        json_data TEXT NOT NULL,
        fetched_at DATETIME DEFAULT CURRENT_TIMESTAMP
    );
    """
}

class QuantDB:
    def __init__(self, db_path: Path):
        self.db_path = db_path
        # Ensure directory exists
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        
        # PRAGMA journal_mode=WAL for concurrent read safety
        self.conn.execute("PRAGMA journal_mode=WAL;")
        
        self.init_schema()
        
    def _get_schema_version(self) -> int:
        self.conn.execute("""
            CREATE TABLE IF NOT EXISTS schema_version (
                version INTEGER
            );
        """)
        row = self.conn.execute("SELECT version FROM schema_version LIMIT 1").fetchone()
        if not row:
            self.conn.execute("INSERT INTO schema_version (version) VALUES (0)")
            self.conn.commit()
            return 0
        return row['version']

    def _set_schema_version(self, version: int):
        self.conn.execute("UPDATE schema_version SET version = ?", (version,))
        self.conn.commit()

    def init_schema(self):
        current_version = self._get_schema_version()
        for version in sorted(MIGRATIONS.keys()):
            if version > current_version:
                self.conn.executescript(MIGRATIONS[version])
                self._set_schema_version(version)
                
    def upsert_position(self, ticker: str, shares: float, avg_cost: float):
        with self.conn:
            self.conn.execute("""
                INSERT INTO portfolio_positions (ticker, shares, avg_cost)
                VALUES (?, ?, ?)
                ON CONFLICT(ticker) DO UPDATE SET
                    shares = excluded.shares,
                    avg_cost = excluded.avg_cost
            """, (ticker, shares, avg_cost))
            
            self.conn.execute("""
                INSERT INTO position_history (ticker, shares, avg_cost)
                VALUES (?, ?, ?)
            """, (ticker, shares, avg_cost))

    def get_positions(self) -> List[PortfolioPosition]:
        cursor = self.conn.execute("SELECT ticker, shares, avg_cost FROM portfolio_positions")
        return [PortfolioPosition(**dict(row)) for row in cursor.fetchall()]

    def upsert_macro(self, metric: MacroMetric):
        with self.conn:
            self.conn.execute("""
                INSERT INTO macro_metrics (indicator_id, value, last_updated)
                VALUES (?, ?, CURRENT_TIMESTAMP)
                ON CONFLICT(indicator_id) DO UPDATE SET
                    value = excluded.value,
                    last_updated = CURRENT_TIMESTAMP
            """, (metric.indicator_id, metric.value))

    def get_macro_snapshot(self) -> Dict[str, MacroMetric]:
        cursor = self.conn.execute("SELECT indicator_id, value, last_updated FROM macro_metrics")
        return {row['indicator_id']: MacroMetric(**dict(row)) for row in cursor.fetchall()}

    def insert_execution_log(self, log: AgentExecutionLog):
        with self.conn:
            # Delete any existing log for today for this ticker to avoid UNIQUE constraint on index
            self.conn.execute("""
                DELETE FROM agent_execution_logs 
                WHERE ticker = ? AND DATE(timestamp) = DATE('now')
            """, (log.ticker,))
            
            self.conn.execute("""
                INSERT INTO agent_execution_logs 
                (run_id, ticker, action, rationale, gemini_tokens, graphiti_tokens, tavily_credits_used)
                VALUES (?, ?, ?, ?, ?, ?, ?)
            """, (log.run_id, log.ticker, log.action, log.rationale, 
                  log.gemini_tokens, log.graphiti_tokens, log.tavily_credits_used))

    def get_total_tokens_today(self) -> int:
        cursor = self.conn.execute("""
            SELECT SUM(gemini_tokens + graphiti_tokens) as total 
            FROM agent_execution_logs 
            WHERE DATE(timestamp) = DATE('now')
        """)
        row = cursor.fetchone()
        return row['total'] if row and row['total'] else 0

    def prune_execution_logs(self, days_to_keep: int = 90):
        with self.conn:
            self.conn.execute(f"DELETE FROM agent_execution_logs WHERE timestamp < datetime('now', '-{days_to_keep} days')")
            self.conn.execute("VACUUM")

    def get_week_analyses(self, ticker: str, days_back: int = 7) -> List[AgentExecutionLog]:
        cursor = self.conn.execute(f"""
            SELECT id, run_id, timestamp, ticker, action, rationale, gemini_tokens, graphiti_tokens, tavily_credits_used
            FROM agent_execution_logs
            WHERE ticker = ? AND timestamp >= datetime('now', '-{days_back} days')
            ORDER BY timestamp ASC
        """, (ticker,))
        return [AgentExecutionLog(**dict(row)) for row in cursor.fetchall()]

    def check_run_completed_today(self) -> bool:
        cursor = self.conn.execute("""
            SELECT 1 FROM agent_execution_logs WHERE DATE(timestamp) = DATE('now') LIMIT 1
        """)
        return cursor.fetchone() is not None

    def get_last_processed_filing(self, ticker: str) -> Optional[str]:
        cursor = self.conn.execute("""
            SELECT accession_number 
            FROM filing_summaries 
            WHERE ticker = ? 
            ORDER BY processed_at DESC 
            LIMIT 1
        """, (ticker,))
        row = cursor.fetchone()
        return row['accession_number'] if row else None

    def store_filing_summary(self, ticker: str, accession_number: str, form_type: str, filing_date: str, summary: str):
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO filing_summaries 
                (ticker, accession_number, form_type, filing_date, summary)
                VALUES (?, ?, ?, ?, ?)
            """, (ticker, accession_number, form_type, filing_date, summary))

    def get_filing_summary(self, ticker: str) -> Optional[str]:
        cursor = self.conn.execute("""
            SELECT summary 
            FROM filing_summaries 
            WHERE ticker = ? 
            ORDER BY processed_at DESC 
            LIMIT 1
        """, (ticker,))
        row = cursor.fetchone()
        return row['summary'] if row else None

    def get_historical_financials(self, ticker: str) -> list[dict]:
        cursor = self.conn.execute("""
            SELECT period_end, revenue, net_income, ebitda, free_cash_flow, total_debt, total_assets
            FROM historical_financials
            WHERE ticker = ?
            ORDER BY period_end DESC
            LIMIT 8
        """, (ticker,))
        return [dict(row) for row in cursor.fetchall()]

    def get_forward_estimates(self, ticker: str) -> list[dict]:
        cursor = self.conn.execute("""
            SELECT period, avg_estimate, low_estimate, high_estimate, number_of_analysts
            FROM forward_estimates
            WHERE ticker = ?
            ORDER BY period ASC
        """, (ticker,))
        return [dict(row) for row in cursor.fetchall()]

    def close(self):
        self.conn.close()

    def get_cached_api_response(self, key: str, max_age_hours: int = 12) -> Optional[str]:
        cursor = self.conn.execute(f"""
            SELECT json_data 
            FROM api_cache 
            WHERE cache_key = ? AND fetched_at >= datetime('now', '-{max_age_hours} hours')
        """, (key,))
        row = cursor.fetchone()
        return row['json_data'] if row else None

    def set_cached_api_response(self, key: str, json_data: str):
        with self.conn:
            self.conn.execute("""
                INSERT OR REPLACE INTO api_cache (cache_key, json_data, fetched_at)
                VALUES (?, ?, CURRENT_TIMESTAMP)
            """, (key, json_data))
