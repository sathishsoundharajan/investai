import time
from pathlib import Path
from typing import Optional, Dict, Any, List
from google import genai
from google.genai import types
from pydantic import ValidationError
from core.config_loader import load_config
from core.logger import get_logger
from memory.schemas import AgentAnalysisOutput
import re

logger = get_logger("llm_client")

class GeminiAnalyst:
    def __init__(self):
        self.config = load_config()
        self.client = genai.Client(api_key=self.config.gemini_api_key) if self.config.gemini_api_key else None
        self.model = self.config.llm_model
        
        # Load prompts
        self.prompts_dir = Path(__file__).parent.parent / "prompts"
        self.filing_prompt_template = self._load_prompt("filing_analysis.txt")
        self.market_prompt_template = self._load_prompt("market_only.txt")

    def _load_prompt(self, filename: str) -> str:
        prompt_path = self.prompts_dir / filename
        if prompt_path.exists():
            return prompt_path.read_text(encoding="utf-8")
        return ""

    def _anonymize(self, text: str) -> str:
        # Prevent any account numbers, SSNs, or brokerage names from leaking
        # Basic demonstration of PII redaction
        text = re.sub(r'\b\d{3}-\d{2}-\d{4}\b', '[REDACTED_SSN]', text)
        text = re.sub(r'(?i)robinhood', '[BROKERAGE]', text)
        return text

    def upload_filing(self, html_path: Path) -> Any:
        if not self.client:
            return None
            
        logger.info(f"Uploading filing {html_path.name} to Gemini Files API")
        file_ref = self.client.files.upload(file=str(html_path))
        
        # Wait for processing
        max_attempts = 12
        for _ in range(max_attempts):
            file_info = self.client.files.get(name=file_ref.name)
            if file_info.state == "ACTIVE":
                return file_info
            elif file_info.state == "FAILED":
                logger.error(f"File upload failed for {html_path.name}")
                return None
            time.sleep(5)
            
        logger.error(f"File upload timeout for {html_path.name}")
        return None

    def cleanup_file(self, file_ref: Any):
        if not self.client or not file_ref:
            return
        try:
            self.client.files.delete(name=file_ref.name)
            logger.info(f"Deleted file {file_ref.name} from Gemini Files API")
        except Exception as e:
            logger.warning(f"Failed to delete file {file_ref.name}: {e}")

    def analyze_filing(self, 
                       ticker: str, 
                       position: Dict[str, Any], 
                       market: Dict[str, Any], 
                       macro: Dict[str, float], 
                       memory_context: str,
                       fundamentals_context: str,
                       news: List[Dict[str, str]],
                       macro_news: List[Dict[str, str]] = [],
                       file_ref: Optional[Any] = None,
                       cached_filing_context: str = "") -> tuple[Optional[AgentAnalysisOutput], int]:
        
        if not self.client:
            logger.warning("Gemini API key not set. Skipping analysis.")
            return None, 0
            
        # Build prompt
        template = self.filing_prompt_template if file_ref else self.market_prompt_template
        
        # Anonymize context
        pos_str = self._anonymize(str(position))
        market_str = self._anonymize(str(market))
        macro_str = self._anonymize(str(macro))
        mem_str = self._anonymize(memory_context)
        news_str = self._anonymize(str(news))
        
        kwargs = {
            "ticker": ticker,
            "position": pos_str,
            "market": market_str,
            "macro": macro_str,
            "macro_news": self._anonymize(macro_news if isinstance(macro_news, str) else str(macro_news)) if macro_news else "No macro news provided.",
            "memory": mem_str,
            "fundamentals_context": self._anonymize(fundamentals_context),
            "news": news_str,
        }
        
        if file_ref:
            prompt_text = template.format(**kwargs)
        else:
            prompt_text = template.format(**kwargs, filing_summary=self._anonymize(cached_filing_context))
            
        contents = [prompt_text]
        if file_ref:
            contents.insert(0, file_ref)

        # Pre-flight token count with dynamic news truncation
        try:
            token_count = self.client.models.count_tokens(model=self.model, contents=contents)
            total_prompt_tokens = token_count.total_tokens
            logger.info(f"Pre-flight token count: {total_prompt_tokens} tokens")
            
            if total_prompt_tokens > 15000:
                logger.warning(f"Prompt token count {total_prompt_tokens} exceeds safety limit of 15000. Truncating news context.")
                # Truncate news list and rebuild prompt
                truncated_news = news[:len(news)//2] if len(news) > 1 else news
                
                if isinstance(macro_news, str):
                    truncated_macro_news = macro_news[:len(macro_news)//2]
                else:
                    truncated_macro_news = macro_news[:len(macro_news)//2] if len(macro_news) > 1 else macro_news
                
                news_str = self._anonymize(str(truncated_news))
                kwargs["news"] = news_str
                kwargs["macro_news"] = self._anonymize(truncated_macro_news if isinstance(truncated_macro_news, str) else str(truncated_macro_news)) if truncated_macro_news else "No macro news provided."
                
                if file_ref:
                    prompt_text = template.format(**kwargs)
                else:
                    prompt_text = template.format(**kwargs, filing_summary=self._anonymize(cached_filing_context))
                
                contents = [prompt_text]
                if file_ref:
                    contents.insert(0, file_ref)
                    
                # Recheck
                token_count = self.client.models.count_tokens(model=self.model, contents=contents)
                logger.info(f"Pre-flight token count after truncation: {token_count.total_tokens} tokens")
        except Exception as e:
            logger.warning(f"Pre-flight token count failed: {e}")

        config = types.GenerateContentConfig(
            response_mime_type="application/json",
            response_schema=AgentAnalysisOutput,
            temperature=0.2,
        )

        for attempt in range(self.config.system.max_retries):
            try:
                logger.info(f"Generating content for {ticker} (Attempt {attempt+1})")
                response = self.client.models.generate_content(
                    model=self.model,
                    contents=contents,
                    config=config
                )
                
                # Log tokens (if available in response.usage_metadata)
                prompt_tokens = response.usage_metadata.prompt_token_count if response.usage_metadata else 0
                calc_tokens = response.usage_metadata.candidates_token_count if response.usage_metadata else 0
                logger.info(f"Tokens used: Prompt={prompt_tokens}, Candidates={calc_tokens}")
                
                # Parse JSON into Pydantic
                parsed = AgentAnalysisOutput.model_validate_json(response.text)
                
                # Return tuple of analysis and total tokens
                total_tokens = prompt_tokens + calc_tokens
                return parsed, total_tokens

            except ValidationError as e:
                logger.warning(f"Validation error on attempt {attempt+1}: {e}")
                # Append error to prompt for next retry
                prompt_text += f"\n\nPREVIOUS ERROR (fix your JSON): {e}"
                contents[-1] = prompt_text
            except Exception as e:
                logger.error(f"Error during Gemini API call for {ticker}: {e}")
                if "429" in str(e):
                    # Gemini rate limits: "Please retry in X.XXs"
                    # Usually backing off for 60s clears it for the free tier.
                    logger.info("Rate limit hit. Backing off for 60 seconds...")
                    time.sleep(60)
                elif "503" in str(e) or "500" in str(e) or "502" in str(e) or "504" in str(e):
                    sleep_time = 5 * (attempt + 1)
                    logger.info(f"Server error hit. Backing off for {sleep_time} seconds...")
                    time.sleep(sleep_time)
                else:
                    time.sleep(5) # Cooldown before retry

        logger.error(f"Exhausted retries for {ticker}")
        return None, 0
