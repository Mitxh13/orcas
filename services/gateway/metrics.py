"""Gateway metrics with bounded label cardinality."""

from prometheus_client import Counter, Histogram

REQUESTS = Counter(
    "orcas_gateway_requests_total", "Gateway HTTP requests", ["method", "route", "status"]
)
LATENCY = Histogram(
    "orcas_gateway_request_duration_seconds", "Gateway HTTP request duration",
    ["method", "route"], buckets=(0.005, 0.01, 0.025, 0.05, 0.1, 0.25, 0.5, 1, 2, 5, 10),
)
RATE_LIMIT_REJECTIONS = Counter(
    "orcas_gateway_rate_limit_rejections_total", "Complaint submissions rejected per client"
)
CACHE_LOOKUPS = Counter(
    "orcas_gateway_run_cache_lookups_total", "Completed-run cache lookups", ["outcome"]
)
