import pytest
from unittest.mock import patch, MagicMock
from tools.market_fetcher import MarketFetcher
import pandas as pd

def test_fetch_eod_data(monkeypatch):
    fetcher = MarketFetcher()
    
    # Mock yfinance Ticker
    mock_ticker = MagicMock()
    
    mock_ticker.info = {
        "sector": "Technology",
        "industry": "Semiconductors",
        "marketCap": 1000000,
        "trailingPE": 50.0,
        "forwardPE": 30.0,
        "priceToBook": 10.0,
        "dividendYield": 0.01
    }
    
    # Create mock history DataFrame
    data = {
        'Close': [100.0, 105.0],
        'Volume': [1000, 2000]
    }
    df = pd.DataFrame(data)
    mock_ticker.history.return_value = df
    
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda x: mock_ticker)
    
    res = fetcher.fetch_eod_data("NVDA")
    assert res is not None
    assert res.ticker == "NVDA"
    assert res.price == 105.0
    assert res.volume == 2000
    assert res.change_percent == 5.0 # (105-100)/100 * 100

def test_fetch_eod_data_empty(monkeypatch):
    fetcher = MarketFetcher()
    
    mock_ticker = MagicMock()
    mock_ticker.history.return_value = pd.DataFrame()
    
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda x: mock_ticker)
    
    res = fetcher.fetch_eod_data("NVDA")
    assert res is None

def test_market_fetcher_caching(tmp_path, monkeypatch):
    from memory.db_client import QuantDB
    db = QuantDB(tmp_path / "test_quant.db")
    
    fetcher = MarketFetcher(db)
    
    # Enable dev mode config
    class MockConfig:
        class SystemConfig:
            dev_mode = True
        system = SystemConfig()
    import tools.market_fetcher
    monkeypatch.setattr(tools.market_fetcher, "load_config", lambda: MockConfig())
    
    # Set cached value in DB
    import json
    cached_data = {
        "ticker": "NVDA",
        "price": 120.0,
        "volume": 5000,
        "change_percent": 2.5,
        "sector": "Tech",
        "industry": "Chips"
    }
    db.set_cached_api_response("market_data_NVDA", json.dumps(cached_data))
    
    # Call fetch_eod_data, should return cached data without hitting yfinance
    res = fetcher.fetch_eod_data("NVDA")
    assert res is not None
    assert res.price == 120.0
    assert res.volume == 5000
    assert res.sector == "Tech"
    
    # Now try dev_mode = False, should hit yfinance (which we mock to return another price)
    class MockConfigProd:
        class SystemConfig:
            dev_mode = False
        system = SystemConfig()
    monkeypatch.setattr(tools.market_fetcher, "load_config", lambda: MockConfigProd())
    
    mock_ticker = MagicMock()
    mock_ticker.info = {"sector": "Technology"}
    df = pd.DataFrame({'Close': [100.0, 110.0], 'Volume': [1000, 2000]})
    mock_ticker.history.return_value = df
    import yfinance as yf
    monkeypatch.setattr(yf, "Ticker", lambda x: mock_ticker)
    
    res = fetcher.fetch_eod_data("NVDA")
    assert res is not None
    assert res.price == 110.0  # From yfinance mock, not cache
    db.close()
