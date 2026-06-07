import sys
import argparse
import asyncio
from core.config_loader import load_config
from core.orchestrator import run_nightly_pipeline

async def main():
    parser = argparse.ArgumentParser(description="InvestAI Nightly Agent")
    parser.add_argument("--force", action="store_true", help="Force run even if already completed today")
    parser.add_argument("--dry-run", action="store_true", help="Run without mutating state")
    parser.add_argument("--tickers", type=str, help="Comma separated list of tickers to override config")
    
    args = parser.parse_args()
    
    config = load_config()
    if args.dry_run:
        config.system.dry_run = True
        
    if args.tickers:
        config.tickers = [t.strip().upper() for t in args.tickers.split(",")]
        
    exit_code = await run_nightly_pipeline(config, force=args.force)
    sys.exit(exit_code)

if __name__ == "__main__":
    asyncio.run(main())
