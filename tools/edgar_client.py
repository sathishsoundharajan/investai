import time
from pathlib import Path
from edgar import set_identity, Company, Filing
from core.config_loader import load_config
from core.logger import get_logger
from core.logger import get_logger

logger = get_logger("edgar_client")

class EdgarFetcher:
    def __init__(self):
        self.config = load_config()
        set_identity(self.config.edgar_client.user_agent)
        self.sleep_time = 1.0 / max(self.config.edgar_client.rate_limit_calls_per_sec, 1)

    def _wait(self):
        time.sleep(self.sleep_time)

    def get_company(self, ticker: str) -> Company:
        self._wait()
        return Company(ticker)

    def fetch_latest_significant_filing(self, ticker: str) -> Filing | None:
        try:
            company = self.get_company(ticker)
            self._wait()
            filings = company.get_filings()
            if not filings:
                return None
            for f in filings:
                if f.form in ("10-K", "10-Q", "8-K"):
                    return f
            return None
        except Exception as e:
            logger.error(f"Error fetching significant filing for {ticker}: {e}")
            return None

            return None

    def get_filing_text(self, filing: Filing) -> str:
        self._wait()
        try:
            return filing.text()
        except AttributeError:
            return filing.html() # Fallback

    def save_filing_text(self, filing: Filing, ticker: str) -> Path:
        text_content = self.get_filing_text(filing)
        file_path = self.config.paths.temp_filings_dir / f"{ticker}_{filing.form}_{filing.accession_no}.txt"
        with open(file_path, "w", encoding="utf-8") as f:
            f.write(text_content)
        return file_path

if __name__ == "__main__":
    import argparse
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", type=str, required=True)
    args = parser.parse_args()
    
    fetcher = EdgarFetcher()
    print(f"Fetching latest 10-K for {args.ticker}...")
    filing = fetcher.fetch_latest_filing(args.ticker, "10-K")
    if filing:
        print(f"Found: {filing.accession_no} filed on {filing.filing_date}")
        xbrl = fetcher.parse_xbrl_metrics(filing, args.ticker)
        print(f"XBRL: {xbrl}")
    else:
        print("No 10-K found.")
