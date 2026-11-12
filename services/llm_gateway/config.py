"""
LLM Gateway configuration — all values come from environment variables.

Changes vs previous version (M2)
----------------------------------
* Stub routing is now ``ORCAS_STUB_MODE=true`` **only**; an empty key is valid
  (Ollama is keyless).
* ``LLM_API_BASE`` and ``LLM_FALLBACK_API_BASE`` / ``LLM_FALLBACK_API_KEY``
  added so that custom endpoints and Ollama are supported.
* ``LLM_MODEL`` and ``LLM_FALLBACK_MODEL`` have no hard-coded defaults; if
  either is unset in live mode ``/health`` reports a configuration error.
* Pricing is env-driven (``LLM_PRICE_INPUT_PER_M`` / ``LLM_PRICE_OUTPUT_PER_M``
  and fallback equivalents) so that no model name appears in the code.
* ``LLM_FORCE_FAIL=1`` makes all primary calls raise immediately — used by
  Demo #8 to trip the circuit breaker intentionally.
"""

import os


class GatewayConfig:
    # ---- Primary model ------------------------------------------------
    LLM_PROVIDER: str = os.getenv("LLM_PROVIDER", "")
    LLM_MODEL: str = os.getenv("LLM_MODEL", "")          # no hard-coded default
    LLM_API_KEY: str = os.getenv("LLM_API_KEY", "")      # empty = Ollama / keyless
    LLM_API_BASE: str = os.getenv("LLM_API_BASE", "")    # custom or Ollama base URL

    # ---- Fallback model (Ollama or second provider) -------------------
    LLM_FALLBACK_MODEL: str = os.getenv("LLM_FALLBACK_MODEL", "")
    LLM_FALLBACK_API_BASE: str = os.getenv("LLM_FALLBACK_API_BASE", "")
    LLM_FALLBACK_API_KEY: str = os.getenv("LLM_FALLBACK_API_KEY", LLM_API_KEY)

    # ---- Pricing (equivalent list price) ------------------------------
    # Defaults to 0 so the system works without pricing configured.
    LLM_PRICE_INPUT_PER_M: float = float(os.getenv("LLM_PRICE_INPUT_PER_M", "0"))
    LLM_PRICE_OUTPUT_PER_M: float = float(os.getenv("LLM_PRICE_OUTPUT_PER_M", "0"))
    LLM_FALLBACK_PRICE_INPUT_PER_M: float = float(os.getenv("LLM_FALLBACK_PRICE_INPUT_PER_M", "0"))
    LLM_FALLBACK_PRICE_OUTPUT_PER_M: float = float(os.getenv("LLM_FALLBACK_PRICE_OUTPUT_PER_M", "0"))

    # ---- Timeouts -------------------------------------------------------
    # Per-attempt timeout for a single LiteLLM call.
    LLM_TIMEOUT_SECONDS: float = float(os.getenv("LLM_TIMEOUT_SECONDS", "30.0"))
    # Total budget for one /v1/chat request: every attempt, backoff wait and the fallback call
    # fit inside it. The worker's client timeout is this value plus a margin.
    LLM_TOTAL_BUDGET_SECONDS: float = float(os.getenv("LLM_TOTAL_BUDGET_SECONDS", "90.0"))

    LLM_TEMPERATURE: float = float(os.getenv("LLM_TEMPERATURE", "0.0"))
    # Ask the provider for a JSON object. Providers that do not support it ignore the setting.
    LLM_JSON_MODE: bool = os.getenv("LLM_JSON_MODE", "true").lower() in ("true", "1", "yes")

    # ---- Circuit breaker -----------------------------------------------
    REDIS_URL: str = os.getenv("REDIS_URL", "redis://localhost:6379/0")
    BREAKER_FAILURES: int = int(os.getenv("BREAKER_FAILURES", "3"))
    BREAKER_COOLDOWN_S: int = int(os.getenv("BREAKER_COOLDOWN_S", "30"))

    # ---- Stub / fault injection ----------------------------------------
    ORCAS_STUB_MODE: bool = os.getenv("ORCAS_STUB_MODE", "false").lower() in ("true", "1", "yes")
    STUB_SERVICE_URL: str = os.getenv("STUB_SERVICE_URL", "http://localhost:8003")

    # When LLM_FORCE_FAIL=1 the gateway raises on every primary call so the
    # circuit breaker can be tripped intentionally during Demo #8.
    LLM_FORCE_FAIL: bool = os.getenv("LLM_FORCE_FAIL", "0") in ("1", "true", "yes")

    @property
    def is_configured(self) -> bool:
        """True when the gateway has enough config to serve live requests."""
        return bool(self.LLM_MODEL)

    @property
    def missing_config(self) -> str:
        """Human-readable list of missing required fields."""
        missing = []
        if not self.LLM_MODEL:
            missing.append("LLM_MODEL")
        return ", ".join(missing) if missing else ""


config = GatewayConfig()
