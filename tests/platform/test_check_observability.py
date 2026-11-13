"""The observability check must pass on the default stack, which has no cAdvisor."""
import importlib.util
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
_spec = importlib.util.spec_from_file_location(
    "check_observability", ROOT / "scripts/demo/check_observability.py")
observability = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(observability)


def _stack(monkeypatch, healthy_jobs):
    def read_json(url):
        if "/api/v1/targets" in url:
            return {"data": {"activeTargets": [
                {"labels": {"job": job}, "health": "up"} for job in healthy_jobs]}}
        if "/api/dashboards/" in url:
            return {"dashboard": {"uid": "orcas-platform",
                                  "panels": [{"id": number} for number in range(100, 131)]}}
        return {"data": {"result": [{"value": [0, "1"]}]}}

    monkeypatch.setattr(observability, "read_json", read_json)


DEFAULT_JOBS = {"gateway", "retrieval", "llm-gateway", "worker-metrics", "prometheus"}


def test_default_stack_passes_without_the_resources_profile(monkeypatch, capsys):
    _stack(monkeypatch, DEFAULT_JOBS)
    jobs, panels = observability.check("http://prom", "http://grafana", resources=False)
    assert "cadvisor" not in jobs and panels == 31
    assert "--profile resources" in capsys.readouterr().out


def test_resources_profile_requires_cadvisor(monkeypatch):
    _stack(monkeypatch, DEFAULT_JOBS)
    with pytest.raises(ValueError, match="cadvisor"):
        observability.check("http://prom", "http://grafana", resources=True)


def test_application_target_down_fails_either_way(monkeypatch):
    _stack(monkeypatch, DEFAULT_JOBS - {"worker-metrics"})
    with pytest.raises(ValueError, match="worker-metrics"):
        observability.check("http://prom", "http://grafana", resources=False)
