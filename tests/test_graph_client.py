import pytest
import asyncio
from unittest.mock import patch, MagicMock
from memory.graph_client import TemporalMemory
from memory.schemas import AgentAnalysisOutput

@pytest.fixture
def mock_config(monkeypatch):
    class MockSystemConfig:
        graphiti_semaphore_limit = 2
        
    class MockConfig:
        gemini_api_key = "test_key"
        llm_model = "test_model"
        graph_embedder = "test_embedder"
        neo4j_uri = "bolt://localhost:7687"
        neo4j_user = "neo4j"
        neo4j_password = "password"
        system = MockSystemConfig()
        
    import core.config_loader
    monkeypatch.setattr(core.config_loader, "load_config", lambda: MockConfig())
    return MockConfig()

@pytest.mark.asyncio
async def test_init_success(mock_config, monkeypatch):
    mem = TemporalMemory()
    
    async def mock_wait(*args, **kwargs):
        pass
    
    mock_graphiti = MagicMock()
    # graphiti.build_indices_and_constraints is async
    async def mock_build():
        pass
    mock_graphiti.build_indices_and_constraints = mock_build
    
    monkeypatch.setattr(mem, "_wait_for_neo4j", mock_wait)
    
    import memory.graph_client
    monkeypatch.setattr(memory.graph_client, "Graphiti", lambda **kwargs: mock_graphiti)
    
    await mem.initialize()
    assert mem._graphiti is not None

@pytest.mark.asyncio
async def test_init_neo4j_down(mock_config, monkeypatch):
    mem = TemporalMemory()
    
    async def mock_wait(*args, **kwargs):
        raise ConnectionError("Timeout")
    
    monkeypatch.setattr(mem, "_wait_for_neo4j", mock_wait)
    
    await mem.initialize()
    assert mem._graphiti is None

@pytest.mark.asyncio
async def test_add_analysis_offline(mock_config):
    mem = TemporalMemory()
    # offline by default
    import datetime
    out = AgentAnalysisOutput(
        ticker="NVDA", identified_risks=[], management_tone="NEUTRAL",
        current_price=100.0, valuation_sense="FAIR_VALUE",
        short_term_outlook="Stable", long_term_outlook="Growth",
        key_drivers=["PE is 50"],
        decision="HOLD", decision_rationale="test"
    )
    # Should not crash
    await mem.add_analysis(out, datetime.datetime.now())

@pytest.mark.asyncio
async def test_get_context_offline(mock_config):
    mem = TemporalMemory()
    res = await mem.get_historical_context("NVDA")
    assert "offline" in res
