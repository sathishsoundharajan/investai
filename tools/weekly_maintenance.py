import asyncio
import argparse
from datetime import datetime, timedelta
from pathlib import Path
import shutil

from core.config_loader import load_config
from core.logger import get_logger
from memory.db_client import QuantDB
from memory.graph_client import TemporalMemory

logger = get_logger("weekly_maintenance")

def archive_old_reports(reports_dir: Path, keep_days: int = 14):
    if not reports_dir.exists():
        return
        
    archive_dir = reports_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    
    cutoff = datetime.now() - timedelta(days=keep_days)
    
    count = 0
    for f in reports_dir.glob("*.md"):
        try:
            # Assuming report files are named YYYY-MM-DD.md
            report_date = datetime.strptime(f.stem, "%Y-%m-%d")
            if report_date < cutoff:
                shutil.move(str(f), str(archive_dir / f.name))
                count += 1
        except ValueError:
            pass # skip non-date files
            
    logger.info(f"Archived {count} old markdown reports.")

async def main():
    parser = argparse.ArgumentParser(description="InvestAI Weekly Maintenance Job")
    parser.add_argument("--keep-reports-days", type=int, default=14)
    parser.add_argument("--keep-logs-days", type=int, default=90)
    args = parser.parse_args()

    logger.info("Starting weekly maintenance...")
    config = load_config()
    db = QuantDB(config.paths.data_dir / "quantitative.db")
    
    # 1. Archive Reports
    logger.info("Archiving old reports...")
    archive_old_reports(config.paths.reports_dir, args.keep_reports_days)
    
    # 2. Prune SQLite execution logs
    logger.info(f"Pruning DB execution logs older than {args.keep_logs_days} days...")
    db.prune_execution_logs(args.keep_logs_days)
    
    # 3. Roll up Neo4j daily episodes
    logger.info("Connecting to graph memory for weekly roll-up...")
    graph_memory = TemporalMemory()
    await graph_memory.initialize()
    
    if graph_memory._graphiti:
        today = datetime.now().date()
        for ticker in config.tickers:
            logger.info(f"Rolling up week for {ticker}...")
            await graph_memory.roll_up_week(ticker, db, today)
    else:
        logger.warning("Graph memory offline. Skipping graph roll-up.")
        
    await graph_memory.close()
    db.close()
    logger.info("Weekly maintenance complete.")

if __name__ == "__main__":
    asyncio.run(main())
