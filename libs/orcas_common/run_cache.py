"""Completed-run cache bound to the input, index, and prompt files."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

import httpx


def prompt_fingerprint(prompt_dir: Path) -> str:
    digest = hashlib.sha256()
    for path in sorted(prompt_dir.rglob("*.yaml")):
        digest.update(str(path.relative_to(prompt_dir)).encode())
        digest.update(path.read_bytes())
    return digest.hexdigest()


class RedisRunCache:
    def __init__(self, redis_url: str, ttl_seconds: int = 3600,
                 index_url: str = "", prompt_dir: Path | None = None):
        import redis

        self.redis = redis.Redis.from_url(redis_url, socket_timeout=2)
        self.ttl_seconds = ttl_seconds
        self.index_url = index_url.rstrip("/")
        self.prompts = prompt_fingerprint(prompt_dir) if prompt_dir else ""

    def key_for(self, complaint: dict[str, Any]) -> str | None:
        if not self.index_url or not self.prompts:
            return None
        response = httpx.get(f"{self.index_url}/v1/index/info", timeout=2)
        response.raise_for_status()
        version = response.json().get("index_version")
        if not version:
            return None
        normalized = dict(complaint)
        normalized["text"] = re.sub(r"\s+", " ", normalized["text"]).strip().casefold()
        if normalized.get("entity_name_hint"):
            normalized["entity_name_hint"] = re.sub(
                r"\s+", " ", normalized["entity_name_hint"]
            ).strip().casefold()
        material = json.dumps({"complaint": normalized, "index_version": version,
                               "prompts_sha256": self.prompts}, sort_keys=True, separators=(",", ":"))
        return "orcas:run-cache:" + hashlib.sha256(material.encode()).hexdigest()

    def get(self, key: str) -> dict[str, Any] | None:
        raw = self.redis.get(key)
        return json.loads(raw) if raw else None

    def put(self, key: str, determination: dict[str, Any]) -> None:
        self.redis.setex(key, self.ttl_seconds, json.dumps(determination))
