import csv
from pathlib import Path
from datetime import datetime
from pydantic import BaseModel
from collections import defaultdict
from core.config_loader import load_config
from memory.db_client import QuantDB
from core.logger import get_logger

logger = get_logger("portfolio_parser")

class ParseResult(BaseModel):
    upserted: int = 0
    rejected: int = 0
    errors: list[str] = []

class PositionData:
    def __init__(self):
        self.shares = 0.0
        self.total_cost = 0.0

    @property
    def avg_cost(self):
        return self.total_cost / self.shares if self.shares > 0 else 0.0

def process_csv(csv_path: Path, db: QuantDB) -> ParseResult:
    result = ParseResult()
    positions = defaultdict(PositionData)
    
    try:
        with open(csv_path, 'r', encoding='utf-8') as f:
            reader = csv.DictReader(f)
            # Robinhood activity logs are newest first, so we read all and reverse
            rows = list(reader)
            rows.reverse()
            
            for i, row in enumerate(rows):
                try:
                    instrument = row.get("Instrument", "").strip()
                    if not instrument:
                        continue # Skip non-instrument rows like ACH
                        
                    trans_code = row.get("Trans Code", "").strip().upper()
                    if trans_code not in ("BUY", "SELL"):
                        continue
                        
                    qty_str = row.get("Quantity", "0").replace(",", "")
                    if not qty_str: continue
                    qty = float(qty_str)
                    
                    price_str = row.get("Price", "0").replace("$", "").replace(",", "")
                    if not price_str: continue
                    price = float(price_str)
                    
                    pos = positions[instrument]
                    
                    if trans_code == "BUY":
                        pos.shares += qty
                        pos.total_cost += qty * price
                    elif trans_code == "SELL":
                        if pos.shares > 0:
                            avg_cost_before = pos.avg_cost
                            pos.shares -= qty
                            pos.total_cost -= qty * avg_cost_before
                            if pos.shares < 1e-6: # Float precision threshold
                                pos.shares = 0.0
                                pos.total_cost = 0.0
                        else:
                            logger.warning(f"Sell transaction for {instrument} but 0 shares held.")
                except Exception as e:
                    result.rejected += 1
                    err_msg = f"Row {len(rows)-i} rejected in {csv_path.name}: {e}"
                    logger.warning(err_msg)
                    result.errors.append(err_msg)
                    
        # Upsert aggregated positions to DB
        for ticker, pos in positions.items():
            if pos.shares > 0:
                db.upsert_position(ticker, pos.shares, pos.avg_cost)
                result.upserted += 1
                
    except Exception as e:
        logger.error(f"Failed to process CSV {csv_path.name}: {e}")
        result.errors.append(str(e))
        
    return result

def run_parser(db_instance=None):
    config = load_config()
    db = db_instance if db_instance else QuantDB(config.paths.data_dir / "quantitative.db")
    
    imports_dir = config.paths.imports_dir
    archive_dir = imports_dir / "archive"
    archive_dir.mkdir(parents=True, exist_ok=True)
    
    total_result = ParseResult()
    
    for csv_path in imports_dir.glob("*.csv"):
        logger.info(f"Processing {csv_path.name}")
        res = process_csv(csv_path, db)
        total_result.upserted += res.upserted
        total_result.rejected += res.rejected
        total_result.errors.extend(res.errors)
        
        # Move to archive
        timestamp = datetime.now().strftime("%Y%m%dT%H%M%S")
        dest_path = archive_dir / f"{csv_path.stem}_{timestamp}.csv"
        csv_path.rename(dest_path)
        logger.info(f"Archived {csv_path.name} to {dest_path.name}")

    if not db_instance:
        db.close()
    
    logger.info(f"Portfolio parsing completed. Upserted: {total_result.upserted}, Rejected: {total_result.rejected}")
    return total_result

if __name__ == "__main__":
    run_parser()
