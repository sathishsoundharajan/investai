import pytest
from unittest.mock import patch, MagicMock
from tools.news_fetcher import NewsFetcher

@pytest.fixture
def mock_config(monkeypatch):
    class MockConfig:
        tavily_api_key = "test_key"
    import tools.news_fetcher
    monkeypatch.setattr(tools.news_fetcher, "load_config", lambda: MockConfig())
    return MockConfig()

def test_search_company_news(mock_config, monkeypatch):
    fetcher = NewsFetcher()
    
    mock_client = MagicMock()
    mock_client.search.return_value = {"results": [{"title": "News 1"}, {"title": "News 2"}]}
    fetcher.client = mock_client
    
    results, credits = fetcher.search_company_news("NVDA")
    assert credits == 1
    assert len(results) == 2
    assert results[0]["title"] == "News 1"

def test_search_company_news_no_key(monkeypatch):
    class MockConfigNoKey:
        tavily_api_key = ""
    import tools.news_fetcher
    monkeypatch.setattr(tools.news_fetcher, "load_config", lambda: MockConfigNoKey())
    
    fetcher = NewsFetcher()
    results, credits = fetcher.search_company_news("NVDA")
    assert credits == 0
    assert len(results) == 0
