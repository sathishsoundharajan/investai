import pytest
from unittest.mock import MagicMock
from core.llm_client import GeminiAnalyst
from pathlib import Path

@pytest.fixture
def mock_config(monkeypatch):
    class MockSystemConfig:
        max_retries = 3
        
    class MockConfig:
        gemini_api_key = "test_key"
        llm_model = "test_model"
        system = MockSystemConfig()
        
    import core.config_loader
    monkeypatch.setattr(core.config_loader, "load_config", lambda: MockConfig())
    return MockConfig()

def test_anonymize(mock_config, monkeypatch):
    analyst = GeminiAnalyst()
    text = "User SSN is 123-45-6789 and broker is Robinhood."
    result = analyst._anonymize(text)
    assert "123-45-6789" not in result
    assert "Robinhood" not in result
    assert "[REDACTED_SSN]" in result
    assert "[BROKERAGE]" in result

def test_analyze_filing_success(mock_config, monkeypatch):
    # Mock GenAI client
    mock_client = MagicMock()
    mock_response = MagicMock()
    mock_response.text = '{"ticker": "NVDA", "identified_risks": [], "management_tone": "NEUTRAL", "current_price": 100.0, "valuation_sense": "FAIR_VALUE", "short_term_outlook": "Stable", "long_term_outlook": "Growth", "key_drivers": ["PE is 50"], "decision": "HOLD", "decision_rationale": "Looks fine."}'
    mock_response.usage_metadata.prompt_token_count = 100
    mock_response.usage_metadata.candidates_token_count = 50
    mock_client.models.generate_content.return_value = mock_response
    
    import google.genai as genai
    monkeypatch.setattr(genai, "Client", lambda api_key: mock_client)
    
    analyst = GeminiAnalyst()
    analyst.client = mock_client # Manually set because init happened before mock maybe
    
    analysis, tokens = analyst.analyze_filing("NVDA", {}, {}, {}, "mem", "funds", [])
    
    assert analysis is not None
    assert analysis.ticker == "NVDA"
    assert analysis.decision == "HOLD"
    assert tokens == 150
