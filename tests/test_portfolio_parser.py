import pytest
import csv
from pathlib import Path
from tools.portfolio_parser import process_csv, run_parser
from memory.db_client import QuantDB

@pytest.fixture
def setup_env(tmp_path, monkeypatch):
    class MockPaths:
        data_dir = tmp_path
        imports_dir = tmp_path / "imports"
        temp_filings_dir = tmp_path / "temp"
        reports_dir = tmp_path / "reports"
        
    class MockConfig:
        paths = MockPaths()
        
    import tools.portfolio_parser
    monkeypatch.setattr(tools.portfolio_parser, "load_config", lambda: MockConfig())
    MockPaths.imports_dir.mkdir(parents=True)
    return MockPaths, QuantDB(tmp_path / "quantitative.db")

def create_csv(path: Path, rows: list):
    with open(path, 'w', newline='') as f:
        writer = csv.DictWriter(f, fieldnames=["Activity Date", "Instrument", "Trans Code", "Quantity", "Price"])
        writer.writeheader()
        writer.writerows(rows)

def test_process_valid_csv(setup_env):
    paths, db = setup_env
    csv_path = paths.imports_dir / "test.csv"
    create_csv(csv_path, [
        {"Activity Date": "1/3/2026", "Instrument": "NVDA", "Trans Code": "Sell", "Quantity": "0.5", "Price": "110.0"},
        {"Activity Date": "1/2/2026", "Instrument": "AAPL", "Trans Code": "Buy", "Quantity": "5", "Price": "150.50"},
        {"Activity Date": "1/2/2026", "Instrument": "NVDA", "Trans Code": "Buy", "Quantity": "10.5", "Price": "$100.0"}
    ])
    
    res = process_csv(csv_path, db)
    assert res.upserted == 2
    assert res.rejected == 0
    
    positions = db.get_positions()
    assert len(positions) == 2
    nvda = next(p for p in positions if p.ticker == "NVDA")
    assert nvda.shares == 10.0
    assert nvda.avg_cost == 100.0

def test_process_invalid_rows(setup_env):
    paths, db = setup_env
    csv_path = paths.imports_dir / "test.csv"
    create_csv(csv_path, [
        {"Activity Date": "1/2/2026", "Instrument": "NVDA", "Trans Code": "Buy", "Quantity": "-10", "Price": "100.0"}, # Negative doesn't fail but skips or causes error later depending on logic, wait float("-10") is fine, but we didn't add negative check in parser so it'll just subtract. Wait, robinhood quantities are always positive in log, sell is indicated by Trans Code. Let's make an invalid float.
        {"Activity Date": "1/2/2026", "Instrument": "", "Trans Code": "Buy", "Quantity": "5", "Price": "150.50"}, # Empty symbol (skipped)
        {"Activity Date": "1/2/2026", "Instrument": "AAPL", "Trans Code": "Buy", "Quantity": "invalid", "Price": "150"} # invalid float
    ])
    
    res = process_csv(csv_path, db)
    assert res.upserted == 0 # First one will not be upserted because shares <= 0
    assert res.rejected == 1 # The 'invalid' one throws error

def test_run_parser(setup_env):
    paths, db = setup_env
    csv_path = paths.imports_dir / "test.csv"
    create_csv(csv_path, [
        {"Activity Date": "1/2/2026", "Instrument": "NVDA", "Trans Code": "Buy", "Quantity": "10", "Price": "100"}
    ])
    
    res = run_parser()
    assert res.upserted == 1
    
    # Check if moved to archive
    assert not csv_path.exists()
    archive_dir = paths.imports_dir / "archive"
    archived_files = list(archive_dir.glob("*.csv"))
    assert len(archived_files) == 1
    assert "test_" in archived_files[0].name
