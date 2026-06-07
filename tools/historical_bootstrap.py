import yfinance as yf
from core.config_loader import load_config
from core.logger import get_logger
from memory.db_client import QuantDB

logger = get_logger("historical_bootstrap")

def bootstrap_ticker(ticker: str, db: QuantDB):
    logger.info(f"Bootstrapping historical financials for {ticker}")
    stock = yf.Ticker(ticker)
    
    # 1. Quarterly Financials
    try:
        q_fin = stock.quarterly_financials
        q_bs = stock.quarterly_balance_sheet
        q_cf = stock.quarterly_cashflow
        
        # We need to align the dates
        if not q_fin.empty:
            for date in q_fin.columns:
                date_str = date.strftime("%Y-%m-%d")
                
                # Safe extraction helpers
                def get_val(df, key):
                    if not df.empty and key in df.index and date in df.columns:
                        val = df.loc[key, date]
                        import pandas as pd
                        if pd.isna(val): return None
                        return int(val)
                    return None

                revenue = get_val(q_fin, "Total Revenue")
                net_income = get_val(q_fin, "Net Income")
                ebitda = get_val(q_fin, "EBITDA")
                
                fcf = get_val(q_cf, "Free Cash Flow")
                
                total_debt = get_val(q_bs, "Total Debt")
                total_assets = get_val(q_bs, "Total Assets")
                
                try:
                    with db.conn:
                        db.conn.execute("""
                            INSERT OR REPLACE INTO historical_financials 
                            (ticker, period_end, revenue, net_income, ebitda, free_cash_flow, total_debt, total_assets)
                            VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                        """, (ticker, date_str, revenue, net_income, ebitda, fcf, total_debt, total_assets))
                except Exception as e:
                    logger.warning(f"Error inserting financial data for {ticker} on {date_str}: {e}")
    except Exception as e:
        logger.warning(f"Failed to fetch quarterly financials for {ticker}: {e}")
        
    # 2. Earnings Estimate
    try:
        # Some yfinance methods change, try earnings_estimate
        if hasattr(stock, "earnings_estimate"):
            ee = stock.earnings_estimate
            if ee is not None and not ee.empty:
                for period in ee.index:
                    avg_est = ee.loc[period, "avg"] if "avg" in ee.columns else None
                    low_est = ee.loc[period, "low"] if "low" in ee.columns else None
                    high_est = ee.loc[period, "high"] if "high" in ee.columns else None
                    num_analysts = int(ee.loc[period, "numberOfAnalysts"]) if "numberOfAnalysts" in ee.columns else None
                    
                    try:
                        with db.conn:
                            db.conn.execute("""
                                INSERT OR REPLACE INTO forward_estimates
                                (ticker, period, avg_estimate, low_estimate, high_estimate, number_of_analysts)
                                VALUES (?, ?, ?, ?, ?, ?)
                            """, (ticker, str(period), avg_est, low_est, high_est, num_analysts))
                    except Exception as e:
                        logger.warning(f"Error inserting forward estimates for {ticker} {period}: {e}")
    except Exception as e:
        logger.warning(f"Failed to fetch earnings estimate for {ticker}: {e}")

def run_bootstrap():
    config = load_config()
    db = QuantDB(config.paths.data_dir / "quantitative.db")
    
    for ticker in config.tickers:
        bootstrap_ticker(ticker, db)
        
    logger.info("Bootstrap complete.")
    db.close()

if __name__ == "__main__":
    run_bootstrap()
