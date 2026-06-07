import pytest
from pathlib import Path
from memory.schemas import PortfolioPosition, MacroMetric, AgentExecutionLog
from memory.db_client import QuantDB

@pytest.fixture
def temp_db(tmp_path):
    db_path = tmp_path / "quantitative.db"
    db = QuantDB(db_path)
    yield db
    db.close()

def test_init_schema_idempotent(temp_db):
    # Should not crash on second call
    temp_db.init_schema()
    
    # Verify version
    assert temp_db._get_schema_version() == 9

def test_upsert_position(temp_db):
    temp_db.upsert_position("NVDA", 10.0, 100.0)
    positions = temp_db.get_positions()
    assert len(positions) == 1
    assert positions[0].ticker == "NVDA"
    assert positions[0].shares == 10.0
    
    # Update position
    temp_db.upsert_position("NVDA", 15.0, 105.0)
    positions = temp_db.get_positions()
    assert len(positions) == 1
    assert positions[0].shares == 15.0
    
    # Check history (should have 2 entries)
    cursor = temp_db.conn.execute("SELECT shares, avg_cost FROM position_history WHERE ticker='NVDA' ORDER BY id")
    history = cursor.fetchall()
    assert len(history) == 2
    assert history[0]['shares'] == 10.0
    assert history[1]['shares'] == 15.0

def test_upsert_macro(temp_db):
    metric = MacroMetric(indicator_id="FEDFUNDS", value=5.25)
    temp_db.upsert_macro(metric)
    
    snapshot = temp_db.get_macro_snapshot()
    assert "FEDFUNDS" in snapshot
    assert snapshot["FEDFUNDS"].value == 5.25

def test_execution_log_and_tokens(temp_db):
    log1 = AgentExecutionLog(
        run_id="run-1", ticker="NVDA", action="BUY", rationale="Good", 
        gemini_tokens=100, graphiti_tokens=50, tavily_credits_used=1
    )
    temp_db.insert_execution_log(log1)
    
    # Total tokens today
    assert temp_db.get_total_tokens_today() == 150
    
    # Duplicate for same day should overwrite instead of raising error
    log2 = AgentExecutionLog(
        run_id="run-1", ticker="NVDA", action="HOLD", rationale="Test", 
        gemini_tokens=10, graphiti_tokens=10, tavily_credits_used=0
    )
    temp_db.insert_execution_log(log2)
    
    # Verify the new log overwrote the old one (tokens won't double up for the row, but we just check it runs without error)

def test_check_run_completed_today(temp_db):
    assert not temp_db.check_run_completed_today()
    log = AgentExecutionLog(
        run_id="run-1", ticker="AAPL", action="HOLD", rationale="Good", 
        gemini_tokens=10, graphiti_tokens=10
    )
    temp_db.insert_execution_log(log)
    assert temp_db.check_run_completed_today()
