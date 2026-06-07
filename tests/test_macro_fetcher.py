import pytest
from unittest.mock import patch, MagicMock
from tools.macro_fetcher import MacroFetcher
import pandas as pd

@pytest.fixture
def mock_config(monkeypatch):
    class MockConfig:
        fred_api_key = "test_key"
    import tools.macro_fetcher
    monkeypatch.setattr(tools.macro_fetcher, "load_config", lambda: MockConfig())
    return MockConfig()

def test_fetch_indicators(mock_config, monkeypatch):
    fetcher = MacroFetcher()
    
    mock_fred = MagicMock()
    mock_fred.get_series.return_value = pd.Series([1.5, 2.0])
    fetcher.fred = mock_fred
    
    res = fetcher.fetch_indicators()
    assert len(res) == 4
    assert res[0].indicator_id == "FEDFUNDS"
    assert res[0].value == 2.0
    
def test_fetch_indicators_no_key(monkeypatch):
    class MockConfigNoKey:
        fred_api_key = ""
    import tools.macro_fetcher
    monkeypatch.setattr(tools.macro_fetcher, "load_config", lambda: MockConfigNoKey())
    
    fetcher = MacroFetcher()
    res = fetcher.fetch_indicators()
    assert len(res) == 0

def test_macro_fetcher_caching(tmp_path, monkeypatch):
    from memory.db_client import QuantDB
    from tools.macro_fetcher import fetch_and_store_macro
    db = QuantDB(tmp_path / "test_quant.db")
    
    # 1. Put cached value in DB
    import json
    cached_data = [
        {"indicator_id": "FEDFUNDS", "value": 5.5},
        {"indicator_id": "CPIAUCSL", "value": 310.0},
        {"indicator_id": "DGS10", "value": 4.2},
        {"indicator_id": "UNRATE", "value": 3.8}
    ]
    db.set_cached_api_response("fred_indicators", json.dumps(cached_data))
    
    # Call fetch_and_store_macro, it should read from cache and update macro_metrics table
    fetch_and_store_macro(db)
    
    snapshot = db.get_macro_snapshot()
    assert "FEDFUNDS" in snapshot
    assert snapshot["FEDFUNDS"].value == 5.5
    assert snapshot["CPIAUCSL"].value == 310.0
    
    db.close()
