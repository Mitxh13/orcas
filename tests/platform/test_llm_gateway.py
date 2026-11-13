"""LLM gateway retry timing: total budget, rate limits and the fallback call (M2)."""
import asyncio

import pytest
from fastapi import HTTPException

from services.llm_gateway import app as gateway


class Breaker:
    def __init__(self):
        self.failures = 0

    def can_execute(self):
        return True

    def get_state(self):
        return "closed", self.failures

    def record_success(self):
        pass

    def record_failure(self):
        self.failures += 1


class RateLimited(Exception):
    status_code = 429

    def __init__(self, retry_after=None):
        super().__init__("rate limited")
        headers = {} if retry_after is None else {"retry-after": str(retry_after)}
        self.response = type("Response", (), {"headers": headers})()


@pytest.fixture
def live(monkeypatch):
    """A gateway in live mode with a recording clock: sleeps advance time, nothing waits."""
    clock = {"now": 1000.0, "sleeps": []}

    async def sleep(seconds):
        clock["sleeps"].append(seconds)
        clock["now"] += seconds

    monkeypatch.setattr(gateway.time, "time", lambda: clock["now"])
    monkeypatch.setattr(gateway.asyncio, "sleep", sleep)
    monkeypatch.setattr(gateway.random, "uniform", lambda a, b: 0.0)
    monkeypatch.setattr(gateway, "breaker", Breaker())
    monkeypatch.setattr(gateway, "PROM_AVAILABLE", False)
    for name, value in {"ORCAS_STUB_MODE": False, "LLM_MODEL": "provider/model",
                        "LLM_FALLBACK_MODEL": "", "LLM_FORCE_FAIL": False,
                        "LLM_TIMEOUT_SECONDS": 30.0, "LLM_TOTAL_BUDGET_SECONDS": 90.0}.items():
        monkeypatch.setattr(gateway.config, name, value)
    return clock


def _request():
    return gateway.ChatRequest(messages=[gateway.ChatMessage(role="user", content="hi")])


def _reply(model="provider/model", degraded=False):
    return gateway.ChatResponse(
        id="1", model=model, degraded=degraded,
        choices=[gateway.ChatChoice(index=0, message=gateway.ChatMessage(role="assistant", content="{}"))],
        usage=gateway.ChatUsage(prompt_tokens=1, completion_tokens=1, total_tokens=2, cost_usd=0.0))


def _calls(monkeypatch, outcomes, cost_s=0.0, clock=None):
    """Replace the provider call; each outcome is an exception to raise or a reply to return."""
    seen = []

    async def call(model, messages_payload, request, api_key, api_base, is_fallback=False, timeout=None):
        seen.append({"model": model, "timeout": timeout, "fallback": is_fallback})
        if clock is not None:  # a real call is cut off at its timeout
            clock["now"] += min(cost_s, timeout if timeout is not None else cost_s)
        outcome = outcomes[min(len(seen), len(outcomes)) - 1]
        if isinstance(outcome, Exception):
            raise outcome
        return outcome

    monkeypatch.setattr(gateway, "_litellm_call", call)
    return seen


def test_rate_limit_waits_for_retry_after_then_succeeds(live, monkeypatch):
    seen = _calls(monkeypatch, [RateLimited(retry_after=7), _reply()])

    result = asyncio.run(gateway.chat_completion(_request()))

    assert result.model == "provider/model" and len(seen) == 2
    assert live["sleeps"] == [7.0]
    assert gateway.breaker.failures == 0


def test_rate_limit_without_retry_after_backs_off_longer_than_other_errors(live, monkeypatch):
    _calls(monkeypatch, [RateLimited(), RuntimeError("boom"), _reply()])

    asyncio.run(gateway.chat_completion(_request()))

    assert live["sleeps"] == [2.0, 1.0]  # 429: 2s base; other errors: 0.5s base, doubled on attempt 2


def test_retry_after_longer_than_the_budget_stops_retrying_and_tells_the_caller(live, monkeypatch):
    seen = _calls(monkeypatch, [RateLimited(retry_after=600)])

    with pytest.raises(HTTPException) as error:
        asyncio.run(gateway.chat_completion(_request()))

    assert error.value.status_code == 503
    assert error.value.headers == {"Retry-After": "600"}
    assert len(seen) == 1 and live["sleeps"] == []
    assert gateway.breaker.failures == 1


def test_attempts_are_cut_short_so_the_request_fits_the_total_budget(live, monkeypatch):
    monkeypatch.setattr(gateway.config, "LLM_FALLBACK_MODEL", "provider/fallback")
    seen = _calls(monkeypatch, [asyncio.TimeoutError()], cost_s=30.0, clock=live)

    with pytest.raises(HTTPException):
        asyncio.run(gateway.chat_completion(_request()))

    assert live["now"] - 1000.0 == pytest.approx(90.0), "never outlives LLM_TOTAL_BUDGET_SECONDS"
    assert [c["timeout"] for c in seen] == [30.0, 30.0, pytest.approx(28.5)]
    assert not any(c["fallback"] for c in seen), "no time left for the fallback"


def test_fallback_runs_inside_what_is_left_of_the_budget(live, monkeypatch):
    monkeypatch.setattr(gateway.config, "LLM_FALLBACK_MODEL", "provider/fallback")
    seen = _calls(monkeypatch, [asyncio.TimeoutError()], cost_s=20.0, clock=live)

    with pytest.raises(HTTPException):
        asyncio.run(gateway.chat_completion(_request()))

    assert [c["fallback"] for c in seen] == [False, False, False, True]
    assert seen[-1]["timeout"] == pytest.approx(28.5)  # 90 - (20 + 0.5 + 20 + 1 + 20)
    assert live["now"] - 1000.0 <= 90.0


def test_fallback_is_skipped_when_no_budget_is_left(live, monkeypatch):
    monkeypatch.setattr(gateway.config, "LLM_FALLBACK_MODEL", "provider/fallback")
    monkeypatch.setattr(gateway.config, "LLM_TOTAL_BUDGET_SECONDS", 50.0)
    seen = _calls(monkeypatch, [asyncio.TimeoutError()], cost_s=30.0, clock=live)

    with pytest.raises(HTTPException) as error:
        asyncio.run(gateway.chat_completion(_request()))

    assert error.value.headers is None
    assert [c["fallback"] for c in seen] == [False, False]
    assert seen[1]["timeout"] == pytest.approx(19.5)  # what is left after 30 s and a 0.5 s wait


def test_fallback_reply_is_marked_degraded(live, monkeypatch):
    monkeypatch.setattr(gateway.config, "LLM_FALLBACK_MODEL", "provider/fallback")
    _calls(monkeypatch, [RuntimeError("down"), RuntimeError("down"), RuntimeError("down"),
                         _reply("provider/fallback", degraded=True)])

    result = asyncio.run(gateway.chat_completion(_request()))

    assert result.degraded is True and result.model == "provider/fallback"
