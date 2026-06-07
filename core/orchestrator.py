import asyncio
import uuid
import time
from datetime import datetime
from pathlib import Path
from core.config_loader import AppConfig
from core.logger import get_logger
from memory.db_client import QuantDB
from memory.graph_client import TemporalMemory
from memory.schemas import AgentExecutionLog
from tools.portfolio_parser import run_parser
from tools.macro_fetcher import fetch_and_store_macro, MacroFetcher
from tools.news_fetcher import NewsFetcher
from tools.market_fetcher import MarketFetcher
from tools.edgar_client import EdgarFetcher
from core.llm_client import GeminiAnalyst

logger = get_logger("orchestrator")

def slim_news(articles: list[dict]) -> list[dict]:
    if not articles:
        return []
    return [{"title": a.get("title", ""), "content": a.get("content", "")[:500]} for a in articles]

async def run_nightly_pipeline(config: AppConfig, force: bool = False) -> int:
    run_id = str(uuid.uuid4())
    logger.info(f"Starting nightly pipeline run: {run_id}")
    
    db = QuantDB(config.paths.data_dir / "quantitative.db")
    
    if not force and db.check_run_completed_today():
        logger.info("Pipeline already ran today. Use --force to override.")
        db.close()
        return 0

    # Initialize external clients
    macro_fetcher = MacroFetcher()
    news_fetcher = NewsFetcher(db)
    market_fetcher = MarketFetcher(db)
    edgar_fetcher = EdgarFetcher()
    llm_client = GeminiAnalyst()
    graph_memory = TemporalMemory()
    await graph_memory.initialize()

    # 1. INIT - Update Portfolio
    logger.info("Running portfolio parser...")
    run_parser(db)
    positions = db.get_positions()
    pos_dict = {p.ticker: {"shares": p.shares, "avg_cost": p.avg_cost} for p in positions}

    # 2. Fetch Macro & News
    logger.info("Fetching macro context...")
    fetch_and_store_macro(db)
    macro_snapshot = db.get_macro_snapshot()
    macro_news, macro_credits = news_fetcher.search_macro_news()
    macro_news_summary = "; ".join([a.get("title", "") for a in macro_news[:3]]) if macro_news else "No macro news provided."

    # Budget constraints
    MAX_DAILY_TOKENS = 250000
    MAX_DAILY_TAVILY = 1000

    # Auto-bootstrap historical financials if empty or stale (older than 45 days)
    from tools.historical_bootstrap import bootstrap_ticker
    latest = db.conn.execute("SELECT MAX(period_end) as latest FROM historical_financials").fetchone()
    if not latest or not latest['latest'] or (datetime.now().date() - datetime.strptime(latest['latest'], '%Y-%m-%d').date()).days > 45:
        logger.info("Historical financials stale or empty. Auto-bootstrapping now...")
        for ticker in config.tickers:
            bootstrap_ticker(ticker, db)

    # 3. Ticker Loop
    tickers = config.tickers
    logger.info(f"Processing {len(tickers)} tickers: {tickers}")
    
    reports = []
    filing_refs = {}
    
    success_count = 0
    failure_count = 0
    
    for idx, ticker in enumerate(tickers):
        logger.info(f"--- Processing {ticker} ---")
        
        # Budget circuit breaker
        tokens_used = db.get_total_tokens_today()
        if tokens_used >= MAX_DAILY_TOKENS:
            logger.error(f"Daily token limit reached ({tokens_used}/{MAX_DAILY_TOKENS}). Stopping pipeline.")
            failure_count += len(tickers) - idx
            break

        try:
            # 3a. Market Data
            market_data = market_fetcher.fetch_eod_data(ticker)
            market_dict = {k: v for k, v in market_data.model_dump().items() if v is not None} if market_data else {}

            # 3b. SEC Edgar
            filing = edgar_fetcher.fetch_latest_significant_filing(ticker)
            file_ref = None
            cached_filing_context = ""
            txt_path = None
            
            if filing:
                last_processed = db.get_last_processed_filing(ticker)
                
                if filing.accession_no != last_processed:
                    logger.info(f"New {filing.form} detected for {ticker} (accession: {filing.accession_no})")
                    txt_path = edgar_fetcher.save_filing_text(filing, ticker)
                    file_ref = llm_client.upload_filing(txt_path)
                    filing_refs[ticker] = f"{filing.form} (filed on {filing.filing_date}) [NEW]"
                else:
                    logger.info(f"No new filing for {ticker}. Using cached {filing.form} summary.")
                    cached_filing_context = db.get_filing_summary(ticker) or ""
                    filing_refs[ticker] = f"{filing.form} (filed on {filing.filing_date}) [CACHED]"
            
            # 3c. Graphiti Context
            memory_context = await graph_memory.get_historical_context(ticker)
            
            # 3d. News
            ticker_news, t_credits = news_fetcher.search_company_news(ticker)
            slimmed_ticker_news = slim_news(ticker_news)

            # 3e. Evaluate
            position_data = pos_dict.get(ticker, {"shares": 0.0, "avg_cost": 0.0})
            macro_data = {k: v.value for k, v in macro_snapshot.items()}
            
            # Fetch historical financials
            hist_fin = db.get_historical_financials(ticker)
            forward_est = db.get_forward_estimates(ticker)
            
            def fmt_money(val):
                if val is None: return "N/A"
                if val >= 1_000_000_000: return f"${val/1_000_000_000:.1f}B"
                if val >= 1_000_000: return f"${val/1_000_000:.1f}M"
                return f"${val}"
                
            fundamentals_trend = "Historical Financials (8Q): Period | Rev | NI | EBITDA | FCF\n"
            for row in hist_fin:
                fundamentals_trend += f"  {row['period_end']} | {fmt_money(row['revenue'])} | {fmt_money(row['net_income'])} | {fmt_money(row['ebitda'])} | {fmt_money(row['free_cash_flow'])}\n"
                
            forward_trend = "Forward Estimates:\n"
            for row in forward_est:
                forward_trend += f"  - {row['period']}: Avg={row['avg_estimate']}, Low={row['low_estimate']}, High={row['high_estimate']}, Analysts={row['number_of_analysts']}\n"
            
            fundamentals_context = fundamentals_trend + "\n" + forward_trend
            
            if config.system.dry_run:
                logger.info(f"DRY RUN: Skipping Gemini LLM call for {ticker}.")
                from memory.schemas import AgentAnalysisOutput
                analysis = AgentAnalysisOutput(
                    ticker=ticker,
                    identified_risks=[],
                    management_tone="NEUTRAL",
                    current_price=market_dict.get("price", 0.0),
                    valuation_sense="FAIR_VALUE",
                    short_term_outlook="Dry run outlook",
                    long_term_outlook="Dry run outlook",
                    key_drivers=["Dry run driver"],
                    decision="HOLD",
                    decision_rationale="This is a dry run test."
                )
                gemini_tokens = 0
            else:
                analysis, gemini_tokens = llm_client.analyze_filing(
                    ticker=ticker,
                    position=position_data,
                    market=market_dict,
                    macro=macro_data,
                    memory_context=memory_context,
                    fundamentals_context=fundamentals_context,
                    news=slimmed_ticker_news,
                    macro_news=macro_news_summary,
                    file_ref=file_ref,
                    cached_filing_context=cached_filing_context
                )

            # 3f. Mutate State
            if file_ref:
                llm_client.cleanup_file(file_ref)
                
            if txt_path and txt_path.exists():
                try:
                    txt_path.unlink()
                except Exception as e:
                    logger.warning(f"Failed to delete local temp filing {txt_path}: {e}")
                
            if analysis:
                if filing and file_ref:
                    # Summarize the analysis for the filing cache (do not store full model JSON to avoid self-reference)
                    summary = f"Filing Type: {filing.form}, Date: {filing.filing_date}\n"
                    summary += f"Management Tone: {analysis.management_tone}\n"
                    if analysis.identified_risks:
                        severity_map = {"HIGH": 0, "MEDIUM": 1, "LOW": 2}
                        top_risks = sorted(analysis.identified_risks, key=lambda r: severity_map.get(r.severity, 2))[:5]
                        summary += f"Identified Risks: {', '.join(r.risk_name for r in top_risks)}\n"
                    
                    db.store_filing_summary(
                        ticker, filing.accession_no, filing.form,
                        filing.filing_date, summary
                    )
                    
                logger.info(f"Analysis for {ticker}: {analysis.decision} - {analysis.decision_rationale}")
                # Save to Graphiti
                if config.system.dev_mode:
                    logger.info(f"DEV MODE: Skipping graph memory insertion for {ticker}.")
                else:
                    last_log = db.get_week_analyses(ticker, days_back=1)
                    if last_log and last_log[-1].action == analysis.decision:
                        logger.info(f"No change for {ticker} ({analysis.decision}). Skipping graph write.")
                    else:
                        await graph_memory.add_analysis(analysis, datetime.now())
                
                # Save to DB
                log = AgentExecutionLog(
                    run_id=run_id,
                    ticker=ticker,
                    action=analysis.decision,
                    rationale=analysis.decision_rationale,
                    gemini_tokens=gemini_tokens,
                    graphiti_tokens=0, # Hidden internally by graphiti_core
                    tavily_credits_used=t_credits + macro_credits
                )
                try:
                    db.insert_execution_log(log)
                except Exception as e:
                    logger.warning(f"Could not save execution log for {ticker}: {e}")
                    
                reports.append(analysis)
                success_count += 1
            else:
                logger.error(f"Failed to generate analysis for {ticker}")
                failure_count += 1

            # 3g. Cooldown
            if idx < len(tickers) - 1:
                sleep_time = config.system.sleep_between_tickers_sec
                logger.info(f"Cooldown: sleeping for {sleep_time}s to respect rate limits...")
                await asyncio.sleep(sleep_time)
                
        except Exception as e:
            logger.exception(f"Unhandled error processing {ticker}: {e}")
            failure_count += 1

    # 4. Report Generation
    logger.info("Generating reports...")
    generate_report(reports, pos_dict, macro_snapshot, filing_refs, config.paths.reports_dir)
    
    await graph_memory.close()
    db.close()
    
    if len(tickers) == 0:
        exit_code = 0
    elif failure_count == 0:
        exit_code = 0
    elif success_count == 0:
        exit_code = 2
    else:
        exit_code = 1
        
    logger.info(f"Pipeline completed. Exit code: {exit_code}")
    return exit_code

def generate_report(analyses: list, pos_dict: dict, macro_snapshot: dict, filing_refs: dict, reports_dir: Path):
    if not analyses:
        return
        
    reports_dir.mkdir(parents=True, exist_ok=True)
    today = datetime.now().strftime("%Y-%m-%d")
    report_path = reports_dir / f"{today}.md"
    
    ticker_sections = {}
    header_content = ""
    
    if report_path.exists():
        try:
            content = report_path.read_text(encoding="utf-8")
            parts = content.split("## Portfolio Overview")
            if len(parts) >= 2:
                header_content = parts[0] + "## Portfolio Overview\n\n"
                portfolio_part = parts[1]
                sec_parts = portfolio_part.split("### ")
                for part in sec_parts:
                    part = part.strip()
                    if not part:
                        continue
                    if part.endswith("---"):
                        part = part[:-3].strip()
                    first_line = part.split("\n")[0]
                    if ":" in first_line:
                        t = first_line.split(":")[0].strip()
                        ticker_sections[t] = "### " + part
        except Exception as e:
            logger.warning(f"Error reading or parsing existing report: {e}")
            
    if not header_content:
        header_content = f"# InvestAI Daily Report - {today}\n\n"
        header_content += "## Macroeconomic Environment\n"
        for indicator, metric in macro_snapshot.items():
            header_content += f"- **{indicator}:** {metric.value}\n"
        header_content += "\n---\n\n"
        header_content += "## Portfolio Overview\n\n"
        
    for a in analyses:
        pos = pos_dict.get(a.ticker, {"shares": 0.0, "avg_cost": 0.0})
        shares = pos["shares"]
        avg_cost = pos["avg_cost"]
        
        # Calculate P&L if they hold the stock
        pnl_str = ""
        if shares > 0:
            cost_basis = shares * avg_cost
            current_value = shares * a.current_price
            pnl = current_value - cost_basis
            pnl_pct = (pnl / cost_basis) * 100 if cost_basis > 0 else 0
            pnl_sign = "+" if pnl >= 0 else ""
            pnl_str = f"**Current Value:** ${current_value:,.2f} ({pnl_sign}${pnl:,.2f} | {pnl_sign}{pnl_pct:.2f}%)\n"
            
        ticker_markdown = f"### {a.ticker}: {a.decision}\n"
        ticker_markdown += f"**Current Price:** ${a.current_price:,.2f} | **Valuation:** {a.valuation_sense}\n"
        if shares > 0:
            ticker_markdown += f"**Your Position:** {shares:,.4f} shares @ ${avg_cost:,.2f} avg cost\n"
            ticker_markdown += pnl_str
        else:
            ticker_markdown += f"**Your Position:** Not currently held.\n"
            
        filing_str = filing_refs.get(a.ticker, "None (Market Data Only)")
        ticker_markdown += f"**SEC Filing Analyzed:** {filing_str}\n"
        
        ticker_markdown += f"\n**Management Tone:** {a.management_tone}\n\n"
        ticker_markdown += f"**Short-Term Outlook:** {a.short_term_outlook}\n\n"
        ticker_markdown += f"**Long-Term Outlook:** {a.long_term_outlook}\n\n"
        ticker_markdown += f"**Recommendation Rationale:** {a.decision_rationale}\n\n"
        
        ticker_markdown += "**Key Drivers (Data Context):**\n"
        for driver in a.key_drivers:
            ticker_markdown += f"- {driver}\n"
        ticker_markdown += "\n"
        
        ticker_markdown += "**Key Risks:**\n"
        if not a.identified_risks:
            ticker_markdown += "- No significant risks identified.\n"
        for r in a.identified_risks:
            ticker_markdown += f"- **{r.risk_name}** ({r.severity}, {r.status}): {r.direct_quote}\n"
            
        ticker_sections[a.ticker] = ticker_markdown
        
    try:
        with open(report_path, "w", encoding="utf-8") as f:
            f.write(header_content)
            for ticker, sec_body in ticker_sections.items():
                f.write(sec_body.strip() + "\n\n---\n\n")
    except Exception as e:
        logger.error(f"Failed to write report file: {e}")
