import pytest
from unittest.mock import patch, MagicMock
from tools.edgar_client import EdgarFetcher

@pytest.fixture
def mock_config(monkeypatch):
    class MockConfig:
        class EdgarClientConfig:
            user_agent = "TestUserAgent (test@example.com)"
            rate_limit_calls_per_sec = 10
        edgar_client = EdgarClientConfig()
    import core.config_loader
    monkeypatch.setattr(core.config_loader, "load_config", lambda: MockConfig())
    return MockConfig()

def test_fetch_latest_filing(mock_config, monkeypatch):
    fetcher = EdgarFetcher()
    
    mock_company = MagicMock()
    mock_filing = MagicMock()
    mock_filing.form = "10-K"
    mock_filing.accession_no = "0001"
    mock_filing.filing_date = "2026-01-01"
    mock_company.get_filings.return_value = [mock_filing]
    
    monkeypatch.setattr(fetcher, "get_company", lambda x: mock_company)
    
    res = fetcher.fetch_latest_significant_filing("NVDA")
    assert res is not None
    assert res.accession_no == "0001"

def test_fetch_latest_filing_none(mock_config, monkeypatch):
    fetcher = EdgarFetcher()
    
    mock_company = MagicMock()
    mock_company.get_filings.return_value = []
    
    monkeypatch.setattr(fetcher, "get_company", lambda x: mock_company)
    
    res = fetcher.fetch_latest_significant_filing("NVDA")
    assert res is None
