from pydantic import BaseModel, Field
from typing import Literal, List, Optional
from datetime import datetime

# --- Database Schemas ---

class PortfolioPosition(BaseModel):
    ticker: str
    shares: float
    avg_cost: float

class PositionHistory(BaseModel):
    id: Optional[int] = None
    timestamp: Optional[datetime] = None
    ticker: str
    shares: float
    avg_cost: float

class MacroMetric(BaseModel):
    indicator_id: str                     # e.g., 'FEDFUNDS', 'CPIAUCSL'
    value: float
    last_updated: Optional[datetime] = None

class AgentExecutionLog(BaseModel):
    id: Optional[int] = None
    run_id: str
    timestamp: Optional[datetime] = None
    ticker: str
    action: Literal["BUY", "HOLD", "REDUCE", "SELL"]
    rationale: str
    gemini_tokens: int
    graphiti_tokens: int
    tavily_credits_used: int = 0

# --- LLM Output Schemas ---

class ExtractedRisk(BaseModel):
    risk_name: str
    severity: Literal["HIGH", "MEDIUM", "LOW"]
    status: Literal["NEW", "ONGOING", "RESOLVED", "WORSENING"]
    direct_quote: str

class AgentAnalysisOutput(BaseModel):
    ticker: str
    identified_risks: List[ExtractedRisk]
    management_tone: Literal["HAWKISH", "DOVISH", "NEUTRAL", "PANICKED"]
    
    # --- Personal Touch Additions ---
    current_price: float = Field(description="The latest observed stock price from the provided market data.")
    valuation_sense: Literal["UNDERVALUED", "FAIR_VALUE", "OVERVALUED"] = Field(description="Assessment of valuation at the stock, sector, and macro level.")
    short_term_outlook: str = Field(description="Projected value/outlook for the short term (1-3 months).")
    long_term_outlook: str = Field(description="Projected value/outlook for the long term (1-3 years).")
    key_drivers: List[str] = Field(description="List of 3-5 specific data points (from fundamentals, macro, or filings) that drove this decision. You MUST cite specific numbers/facts.")
    
    decision: Literal["BUY", "HOLD", "REDUCE", "SELL"]
    decision_rationale: str = Field(description="Max 3 sentences justifying the decision, making sure to reference the user's specific holding quantity and cost basis.")
