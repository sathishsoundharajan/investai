import yfinance as yf
from pydantic import BaseModel
from typing import Optional
from core.config_loader import load_config
from core.logger import get_logger
from memory.db_client import QuantDB

logger = get_logger("market_fetcher")

class MarketData(BaseModel):
    ticker: str
    price: float
    volume: int
    change_percent: float
    
    # Fundamentals
    sector: Optional[str] = None
    industry: Optional[str] = None
    market_cap: Optional[int] = None
    trailing_pe: Optional[float] = None
    forward_pe: Optional[float] = None
    price_to_book: Optional[float] = None
    dividend_yield: Optional[float] = None

class MarketFetcher:
    def __init__(self, db: Optional[QuantDB] = None):
        self.db = db

    def fetch_eod_data(self, ticker: str) -> Optional[MarketData]:
        config = load_config()
        cache_key = f"market_data_{ticker}"
        
        if config.system.dev_mode and self.db:
            cached = self.db.get_cached_api_response(cache_key, max_age_hours=1)
            if cached:
                logger.info(f"Using cached market data for {ticker}")
                try:
                    import json
                    data_dict = json.loads(cached)
                    return MarketData(**data_dict)
                except Exception as e:
                    logger.warning(f"Failed to parse cached market data: {e}")

        try:
            ticker_obj = yf.Ticker(ticker)
            # fetch last 2 days to calculate change percent
            hist = ticker_obj.history(period="5d")
            if hist.empty or len(hist) < 2:
                logger.warning(f"No recent history found for {ticker}")
                return None
                
            last_close = float(hist['Close'].iloc[-1])
            prev_close = float(hist['Close'].iloc[-2])
            volume = int(hist['Volume'].iloc[-1])
            
            change_percent = ((last_close - prev_close) / prev_close) * 100.0
            info = ticker_obj.info
            
            data = MarketData(
                ticker=ticker,
                price=last_close,
                volume=volume,
                change_percent=change_percent,
                sector=info.get("sector"),
                industry=info.get("industry"),
                market_cap=info.get("marketCap"),
                trailing_pe=info.get("trailingPE"),
                forward_pe=info.get("forwardPE"),
                price_to_book=info.get("priceToBook"),
                dividend_yield=info.get("dividendYield")
            )
            
            if config.system.dev_mode and self.db:
                import json
                self.db.set_cached_api_response(cache_key, json.dumps(data.model_dump()))
                
            return data
        except Exception as e:
            logger.error(f"Error fetching market data for {ticker}: {e}")
            return None

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, required=True)
    args = parser.parse_args()
    
    fetcher = MarketFetcher()
    data = fetcher.fetch_eod_data(args.ticker)
    print(data)
