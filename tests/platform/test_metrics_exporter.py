from services.metrics_exporter.app import render_metrics


def test_persisted_metrics_include_latency_cost_and_queue_depth():
    body = render_metrics({
        "statuses": {"completed": 8, "escalated": 1, "failed": 1},
        "latency": (12.5, 31.0),
        "events": (2, 15, 30, 300, 100, 0.25),
        "feedback": {"up": 4, "down": 2},
        "scores": {"citation_validity": 0.9},
        "step_latency": [("search", 100, 450)],
        "llm_by_step": [("draft", 300, 100, 0.25)],
        "queue_depth": 3,
        "uncached_terminal": 10,
        "escalations": [("agent_failure", 1)],
        "degraded": 1,
        "agent_rows": [("researcher", 8, 24, 12, 8, 7), ("verifier", 8, 16, 0, 8, 6)],
        "agent_step_latency": [("verifier", "verify", 420)],
    }).decode()
    assert 'orcas_run_latency_seconds{quantile="0.95"} 31.0' in body
    assert 'orcas_runs_success_ratio 0.9' in body
    assert 'orcas_celery_queue_depth 3.0' in body
    assert 'orcas_run_cost_usd 0.25' in body
    assert 'orcas_cost_per_100_runs_usd 2.5' in body
    assert 'orcas_run_step_latency_seconds{quantile="0.95",step="search"} 0.45' in body
    assert 'orcas_run_tokens_by_step{direction="prompt",step="draft"} 300.0' in body
    assert 'orcas_run_cost_by_step_usd{step="draft"} 0.25' in body
    assert 'orcas_run_retry_rate 0.2' in body
    assert 'orcas_run_escalation_reason{reason="agent_failure"} 1.0' in body
    assert 'orcas_agent_success_ratio{agent="verifier"} 0.75' in body
    assert 'orcas_agent_step_latency_seconds{agent="verifier",step="verify"} 0.42' in body
