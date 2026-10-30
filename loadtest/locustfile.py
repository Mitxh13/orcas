"""Measure complaint submission through terminal status, including queue time."""

from __future__ import annotations

import json
import os
import random
import time
import uuid
from pathlib import Path

from locust import HttpUser, between, events, task

_SOURCE = json.loads((Path(__file__).parent / "payloads.json").read_text(encoding="utf-8"))
PAYLOADS = [
    {key: value for key, value in item.items() if not key.startswith("_")}
    for item in _SOURCE["payloads"]
    if not item.get("_scenario_id", "").startswith("prompt_injection")
]
POLL_SECONDS = float(os.getenv("LOADTEST_POLL_SECONDS", "1"))
MAX_WAIT_SECONDS = float(os.getenv("LOADTEST_MAX_WAIT_SECONDS", "180"))
CACHE_MODE = os.getenv("LOADTEST_CACHE_MODE", "unique")
if CACHE_MODE not in {"unique", "repeat", "bypass"}:
    raise ValueError("LOADTEST_CACHE_MODE must be unique, repeat, or bypass")


class ComplaintUser(HttpUser):
    wait_time = between(0.5, 1.5)

    @task
    def submit_and_poll(self):
        payload = dict(random.choice(PAYLOADS))
        if CACHE_MODE == "unique":
            payload["client_id"] = f"load-{uuid.uuid4().hex[:16]}"
        headers = {"X-Run-Cache-Bypass": "true"} if CACHE_MODE == "bypass" else {}
        started = time.monotonic()
        error: Exception | None = None
        response_length = 0

        try:
            with self.client.post("/v1/complaints", json=payload, headers=headers,
                                  name="POST /v1/complaints",
                                  catch_response=True) as response:
                if response.status_code != 202:
                    response.failure(f"expected 202, got {response.status_code}")
                    raise RuntimeError(f"intake returned {response.status_code}")
                run_id = response.json()["run_id"]
            while time.monotonic() - started < MAX_WAIT_SECONDS:
                with self.client.get(f"/v1/runs/{run_id}", name="GET /v1/runs/{id}",
                                     catch_response=True) as response:
                    if response.status_code != 200:
                        response.failure(f"poll returned {response.status_code}")
                        raise RuntimeError(f"poll returned {response.status_code}")
                    body = response.json()
                    response_length = len(response.content)
                if body.get("status") in {"completed", "escalated"}:
                    break
                if body.get("status") == "failed":
                    raise RuntimeError("run failed")
                time.sleep(POLL_SECONDS)
            else:
                raise TimeoutError(f"run did not finish within {MAX_WAIT_SECONDS}s")
        except Exception as exc:
            error = exc
        finally:
            events.request.fire(
                request_type="ORCAS", name="complaint_end_to_end",
                response_time=(time.monotonic() - started) * 1000,
                response_length=response_length, exception=error, context={},
            )
