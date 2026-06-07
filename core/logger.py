import logging
import sys
from pathlib import Path
from datetime import datetime
from rich.logging import RichHandler
import json

class JSONFormatter(logging.Formatter):
    def format(self, record):
        log_data = {
            "timestamp": datetime.fromtimestamp(record.created).isoformat(),
            "level": record.levelname,
            "module": record.module,
            "message": record.getMessage(),
        }
        if hasattr(record, 'extra') and record.extra:
            log_data['extra'] = record.extra
        if record.exc_info:
            log_data['exc_info'] = self.formatException(record.exc_info)
        return json.dumps(log_data)

def get_logger(name: str) -> logging.Logger:
    logger = logging.getLogger(name)
    
    # Silence Graphiti's noisy index creation errors at the root level if they match the safe error
    class GraphitiIndexFilter(logging.Filter):
        def filter(self, record):
            if "EquivalentSchemaRuleAlreadyExists" in record.getMessage():
                return False
            return True
            
    # Apply filter to graphiti core logger
    graphiti_logger = logging.getLogger("graphiti_core")
    graphiti_logger.addFilter(GraphitiIndexFilter())
    
    if not logger.handlers:
        logger.setLevel(logging.INFO)
        
        # Suppress noisy neo4j schema warnings
        logging.getLogger("neo4j").setLevel(logging.ERROR)
        
        # Terminal handler
        rich_handler = RichHandler(rich_tracebacks=True, markup=True)
        rich_handler.setLevel(logging.INFO)
        rich_formatter = logging.Formatter("%(message)s")
        rich_handler.setFormatter(rich_formatter)
        logger.addHandler(rich_handler)
        
        # File handler
        logs_dir = Path("data/logs")
        logs_dir.mkdir(parents=True, exist_ok=True)
        today = datetime.now().strftime("%Y-%m-%d")
        file_handler = logging.FileHandler(logs_dir / f"investai_{today}.jsonl")
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(JSONFormatter())
        logger.addHandler(file_handler)
        
    return logger
