import time
from typing import Tuple
import redis
from services.llm_gateway.config import config

class CircuitBreaker:
    """
    Shared circuit breaker backed by Redis with local in-memory fallback.
    Prevents hammering failed or rate-limited LLM providers across all workers.
    """
    def __init__(self, key_prefix: str = "orcas:cb:llm"):
        self.key_prefix = key_prefix
        self.state_key = f"{key_prefix}:state"
        self.failures_key = f"{key_prefix}:failures"
        self.last_failure_key = f"{key_prefix}:last_failure"
        
        self._mem_failures = 0
        self._mem_state = "closed"
        self._mem_last_failure = 0.0
        
        self.redis_client = None
        try:
            self.redis_client = redis.from_url(config.REDIS_URL, decode_responses=True)
            self.redis_client.ping()
        except Exception:
            self.redis_client = None

    def get_state(self) -> Tuple[str, int]:
        now = time.time()
        if self.redis_client:
            try:
                state = self.redis_client.get(self.state_key) or "closed"
                failures = int(self.redis_client.get(self.failures_key) or 0)
                last_failure = float(self.redis_client.get(self.last_failure_key) or 0.0)
                
                if state == "open" and (now - last_failure) >= config.BREAKER_COOLDOWN_S:
                    return "half-open", failures
                return state, failures
            except Exception:
                pass

        if self._mem_state == "open" and (now - self._mem_last_failure) >= config.BREAKER_COOLDOWN_S:
            return "half-open", self._mem_failures
        return self._mem_state, self._mem_failures

    def can_execute(self) -> bool:
        state, _ = self.get_state()
        return state in ("closed", "half-open")

    def record_success(self):
        if self.redis_client:
            try:
                pipe = self.redis_client.pipeline()
                pipe.set(self.state_key, "closed")
                pipe.set(self.failures_key, 0)
                pipe.execute()
                return
            except Exception:
                pass
        self._mem_state = "closed"
        self._mem_failures = 0

    def record_failure(self) -> bool:
        now = time.time()
        if self.redis_client:
            try:
                pipe = self.redis_client.pipeline()
                pipe.incr(self.failures_key)
                pipe.set(self.last_failure_key, now)
                results = pipe.execute()
                failures = results[0]
                if failures >= config.BREAKER_FAILURES:
                    self.redis_client.set(self.state_key, "open")
                    return True
                return False
            except Exception:
                pass
        
        self._mem_failures += 1
        self._mem_last_failure = now
        if self._mem_failures >= config.BREAKER_FAILURES:
            self._mem_state = "open"
            return True
        return False
