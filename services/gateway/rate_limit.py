"""Atomic per-client token bucket backed by Redis."""

from __future__ import annotations

import math
import time

_TAKE_TOKEN = """
local key = KEYS[1]
local now = tonumber(ARGV[1])
local capacity = tonumber(ARGV[2])
local refill = capacity / 60
local previous = redis.call('HMGET', key, 'tokens', 'updated')
local tokens = tonumber(previous[1]) or capacity
local updated = tonumber(previous[2]) or now
tokens = math.min(capacity, tokens + math.max(0, now - updated) * refill)
if tokens >= 1 then
  tokens = tokens - 1
  redis.call('HMSET', key, 'tokens', tokens, 'updated', now)
  redis.call('EXPIRE', key, 120)
  return 0
end
redis.call('HMSET', key, 'tokens', tokens, 'updated', now)
redis.call('EXPIRE', key, 120)
return math.ceil((1 - tokens) / refill)
"""


class RedisRateLimiter:
    def __init__(self, url: str, per_minute: int):
        import redis

        if per_minute < 1:
            raise ValueError("per_minute must be positive")
        self.redis = redis.Redis.from_url(url, socket_timeout=2)
        self.per_minute = per_minute

    def retry_after(self, client_id: str) -> int:
        """Return zero when allowed, otherwise whole seconds until another token."""
        seconds = self.redis.eval(
            _TAKE_TOKEN, 1, f"orcas:rate:{client_id}", time.time(), self.per_minute,
        )
        return max(0, math.ceil(int(seconds)))
