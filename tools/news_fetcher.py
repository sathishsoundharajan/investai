import json
from tavily import TavilyClient
from typing import Tuple, List, Dict, Any, Optional
from core.config_loader import load_config
from core.logger import get_logger
from memory.db_client import QuantDB

logger = get_logger("news_fetcher")

class NewsFetcher:
    def __init__(self, db: Optional[QuantDB] = None):
        self.config = load_config()
        self.api_key = self.config.tavily_api_key
        self.client = TavilyClient(api_key=self.api_key) if self.api_key else None
        self.db = db

    def search_company_news(self, ticker: str, days_back: int = 7) -> Tuple[List[Dict[str, Any]], int]:
        """Returns (list_of_results, credits_used)."""
        if not self.client:
            logger.warning("Tavily API key not set. Skipping news fetch.")
            return [], 0
            
        cache_key = f"news_company_{ticker}_{days_back}"
        if self.db:
            cached = self.db.get_cached_api_response(cache_key)
            if cached:
                logger.info(f"Using cached company news for {ticker}")
                return json.loads(cached), 0

        try:
            # Basic search query
            query = f"{ticker} stock news financial performance last {days_back} days"
            response = self.client.search(query=query, search_depth="basic", max_results=5)
            results = response.get('results', [])
            
            if self.db:
                self.db.set_cached_api_response(cache_key, json.dumps(results))
                
            # Tavily basic search uses 1 credit
            return results, 1
        except Exception as e:
            logger.error(f"Error fetching news for {ticker}: {e}")
            return [], 0

    def search_macro_news(self) -> Tuple[List[Dict[str, Any]], int]:
        if not self.client:
            return [], 0
            
        cache_key = "news_macro"
        if self.db:
            cached = self.db.get_cached_api_response(cache_key)
            if cached:
                logger.info("Using cached macro news")
                return json.loads(cached), 0

        try:
            query = "US macroeconomic news interest rates inflation fed policy"
            response = self.client.search(query=query, search_depth="basic", max_results=5)
            results = response.get('results', [])
            
            if self.db:
                self.db.set_cached_api_response(cache_key, json.dumps(results))
                
            return results, 1
        except Exception as e:
            logger.error(f"Error fetching macro news: {e}")
            return [], 0

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--query", type=str, required=True)
    args = parser.parse_args()
    
    fetcher = NewsFetcher()
    results = fetcher.client.search(query=args.query, max_results=3) if fetcher.client else {}
    print(results)
