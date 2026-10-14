import os

class GatewayConfig:
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "gemini")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "gemini/gemini-1.5-flash")
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")
    LLM_FALLBACK_MODEL: str = os.getenv("LLM_FALLBACK_MODEL", "groq/llama-3.1-8b-instant")
    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.0"))
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "15.0"))
    
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    BREAKER_FAILURES: int = int(os.getenv("BREAKER_FAILURES", "3"))
    BREAKER_COOLDOWN_S: int = int(os.getenv("BREAKER_COOLDOWN_S", "30"))
    
    ORCAS_STUB_MODE: bool = os.getenv("ORCAS_STUB_MODE", "false").lower() in ("true", "1", "yes")
    STUB_SERVICE_URL: str = os.getenv("STUB_SERVICE_URL", "http://localhost:8003")

config = GatewayConfig()
