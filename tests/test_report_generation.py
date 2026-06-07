import pytest
from pathlib import Path
from datetime import datetime
from memory.schemas import AgentAnalysisOutput, ExtractedRisk, MacroMetric
from core.orchestrator import generate_report

def test_generate_report_appends_and_updates(tmp_path):
    reports_dir = tmp_path / "reports"
    
    # Setup test data
    nvda_analysis = AgentAnalysisOutput(
        ticker="NVDA",
        identified_risks=[
            ExtractedRisk(risk_name="Competition", severity="HIGH", status="NEW", direct_quote="Intense competition")
        ],
        management_tone="NEUTRAL",
        current_price=120.0,
        valuation_sense="FAIR_VALUE",
        short_term_outlook="Flat outlook",
        long_term_outlook="Growth outlook",
        key_drivers=["AI demand"],
        decision="BUY",
        decision_rationale="Buy NVDA now."
    )
    
    sofi_analysis = AgentAnalysisOutput(
        ticker="SOFI",
        identified_risks=[],
        management_tone="NEUTRAL",
        current_price=7.5,
        valuation_sense="UNDERVALUED",
        short_term_outlook="Good outlook",
        long_term_outlook="Strong growth",
        key_drivers=["Fintech adoption"],
        decision="HOLD",
        decision_rationale="Hold SOFI."
    )
    
    pos_dict = {
        "NVDA": {"shares": 10.0, "avg_cost": 100.0},
        "SOFI": {"shares": 100.0, "avg_cost": 8.0}
    }
    
    macro_snapshot = {
        "FEDFUNDS": MacroMetric(indicator_id="FEDFUNDS", value=5.25)
    }
    
    filing_refs = {
        "NVDA": "10-Q [NEW]",
        "SOFI": "10-Q [CACHED]"
    }
    
    # 1. Run for NVDA alone
    generate_report([nvda_analysis], pos_dict, macro_snapshot, filing_refs, reports_dir)
    
    today = datetime.now().strftime("%Y-%m-%d")
    report_path = reports_dir / f"{today}.md"
    assert report_path.exists()
    
    content = report_path.read_text(encoding="utf-8")
    assert "### NVDA: BUY" in content
    assert "### SOFI:" not in content
    
    # 2. Run for SOFI alone
    generate_report([sofi_analysis], pos_dict, macro_snapshot, filing_refs, reports_dir)
    
    content = report_path.read_text(encoding="utf-8")
    assert "### NVDA: BUY" in content  # NVDA should still be there!
    assert "### SOFI: HOLD" in content  # SOFI should now be appended!
    
    # 3. Update NVDA decision to HOLD and run again
    nvda_analysis_updated = AgentAnalysisOutput(
        ticker="NVDA",
        identified_risks=[
            ExtractedRisk(risk_name="Competition", severity="HIGH", status="NEW", direct_quote="Intense competition")
        ],
        management_tone="NEUTRAL",
        current_price=125.0,
        valuation_sense="FAIR_VALUE",
        short_term_outlook="Flat outlook",
        long_term_outlook="Growth outlook",
        key_drivers=["AI demand"],
        decision="HOLD",  # UPDATED DECISION
        decision_rationale="Hold NVDA now."
    )
    
    generate_report([nvda_analysis_updated], pos_dict, macro_snapshot, filing_refs, reports_dir)
    
    content = report_path.read_text(encoding="utf-8")
    assert "### NVDA: HOLD" in content     # Should be updated to HOLD
    assert "### NVDA: BUY" not in content   # The old BUY decision should be gone
    assert "### SOFI: HOLD" in content      # SOFI should still be there!
