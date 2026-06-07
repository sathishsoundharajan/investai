import ssl
from fredapi import Fred
from typing import List
from core.config_loader import load_config
from core.logger import get_logger
from memory.schemas import MacroMetric
from memory.db_client import QuantDB

# Fix for MacOS SSL Certificate errors with urllib
try:
    _create_unverified_https_context = ssl._create_unverified_context
except AttributeError:
    pass
else:
    ssl._create_default_https_context = _create_unverified_https_context

logger = get_logger("macro_fetcher")

class MacroFetcher:
    def __init__(self):
        self.config = load_config()
        self.api_key = self.config.fred_api_key
        self.fred = Fred(api_key=self.api_key) if self.api_key else None
        self.indicators = ["FEDFUNDS", "CPIAUCSL", "DGS10", "UNRATE"]

    def fetch_indicators(self) -> List[MacroMetric]:
        if not self.fred:
            logger.warning("FRED API key not set. Skipping macro fetch.")
            return []
            
        metrics = []
        for ind in self.indicators:
            try:
                series = self.fred.get_series(ind)
                if not series.empty:
                    # Get the most recent value
                    value = float(series.iloc[-1])
                    metrics.append(MacroMetric(indicator_id=ind, value=value))
            except Exception as e:
                logger.error(f"Error fetching FRED indicator {ind}: {e}")
                
        return metrics

def fetch_and_store_macro(db: QuantDB):
    cache_key = "fred_indicators"
    cached = db.get_cached_api_response(cache_key, max_age_hours=24)
    if cached:
        logger.info("Using cached FRED macro indicators.")
        import json
        try:
            metrics_data = json.loads(cached)
            metrics = [MacroMetric(indicator_id=m['indicator_id'], value=m['value']) for m in metrics_data]
            for metric in metrics:
                db.upsert_macro(metric)
            logger.info(f"Updated {len(metrics)} macro indicators in DB from cache.")
            return
        except Exception as e:
            logger.warning(f"Failed to parse cached FRED indicators: {e}")

    fetcher = MacroFetcher()
    metrics = fetcher.fetch_indicators()
    if metrics:
        import json
        metrics_data = [{"indicator_id": m.indicator_id, "value": m.value} for m in metrics]
        db.set_cached_api_response(cache_key, json.dumps(metrics_data))
    for metric in metrics:
        db.upsert_macro(metric)
    logger.info(f"Updated {len(metrics)} macro indicators in DB.")

if __name__ == "__main__":
    fetcher = MacroFetcher()
    metrics = fetcher.fetch_indicators()
    for m in metrics:
        print(f"{m.indicator_id}: {m.value}")
