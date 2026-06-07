import os
from pathlib import Path
from pydantic import BaseModel, Field, ValidationError
from dotenv import load_dotenv
import yaml

class SystemConfig(BaseModel):
    llm_model: str = "gemini-3.1-flash-lite"
    graph_llm_model: str = "gemini-3.1-flash"
    graph_embedder: str = "text-embedding-004"
    graphiti_semaphore_limit: int = 2
    sleep_between_tickers_sec: int = 15
    max_retries: int = 3
    dev_mode: bool = False
    dry_run: bool = False

class Neo4jConfig(BaseModel):
    uri: str = "bolt://localhost:7687"
    user: str = "neo4j"

class EdgarClientConfig(BaseModel):
    user_agent: str
    rate_limit_calls_per_sec: int = 10

class PathsConfig(BaseModel):
    data_dir: Path = Path("./data")
    imports_dir: Path = Path("./data/imports")
    temp_filings_dir: Path = Path("./data/temp_filings")
    reports_dir: Path = Path("./data/reports")

class AppConfig(BaseModel):
    # Secrets from .env
    gemini_api_key: str
    tavily_api_key: str
    fred_api_key: str
    neo4j_password: str
    
    # Sections from config.yaml
    system: SystemConfig = Field(default_factory=SystemConfig)
    neo4j: Neo4jConfig = Field(default_factory=Neo4jConfig)
    edgar_client: EdgarClientConfig
    tickers: list[str]
    paths: PathsConfig = Field(default_factory=PathsConfig)

    # Convenience properties
    @property
    def neo4j_uri(self) -> str: return self.neo4j.uri
    
    @property
    def neo4j_user(self) -> str: return self.neo4j.user
    
    @property
    def graphiti_semaphore_limit(self) -> int: return self.system.graphiti_semaphore_limit

    @property
    def llm_model(self) -> str: return self.system.llm_model

    @property
    def graph_llm_model(self) -> str: return self.system.graph_llm_model

    @property
    def graph_embedder(self) -> str: return self.system.graph_embedder

_config_instance = None

def load_config() -> AppConfig:
    global _config_instance
    if _config_instance is not None:
        return _config_instance
        
    load_dotenv()
    
    with open("config.yaml", "r") as f:
        yaml_data = yaml.safe_load(f)
        
    try:
        config = AppConfig(
            gemini_api_key=os.environ.get("GEMINI_API_KEY", ""),
            tavily_api_key=os.environ.get("TAVILY_API_KEY", ""),
            fred_api_key=os.environ.get("FRED_API_KEY", ""),
            neo4j_password=os.environ.get("NEO4J_PASSWORD", ""),
            system=yaml_data.get("system", {}),
            neo4j=yaml_data.get("neo4j", {}),
            edgar_client=yaml_data.get("edgar_client", {}),
            tickers=yaml_data.get("tickers", []),
            paths=yaml_data.get("paths", {})
        )
        
        # Ensure directories exist
        for path_val in [config.paths.data_dir, config.paths.imports_dir, config.paths.temp_filings_dir, config.paths.reports_dir, config.paths.data_dir / "logs", config.paths.imports_dir / "archive"]:
            path_val.mkdir(parents=True, exist_ok=True)
            
        _config_instance = config
        return _config_instance
    except ValidationError as e:
        print(f"Configuration Validation Error: {e}")
        raise
