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
    }).decode()
    assert 'orcas_run_latency_seconds{quantile="0.95"} 31.0' in body
    assert 'orcas_runs_success_ratio 0.9' in body
    assert 'orcas_celery_queue_depth 3.0' in body
    assert 'orcas_run_cost_usd 0.25' in body
    assert 'orcas_cost_per_100_runs_usd 2.5' in body
    assert 'orcas_run_step_latency_seconds{quantile="0.95",step="search"} 0.45' in body
    assert 'orcas_run_tokens_by_step{direction="prompt",step="draft"} 300.0' in body
    assert 'orcas_run_cost_by_step_usd{step="draft"} 0.25' in body
