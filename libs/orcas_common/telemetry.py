"""OTLP tracing bootstrap shared by HTTP services and the worker."""

from __future__ import annotations

import os

_provider = None
_httpx_instrumented = False


def configure_tracing(service_name: str, app=None, instrument_httpx: bool = False) -> None:
    global _provider, _httpx_instrumented
    endpoint = os.getenv("OTEL_EXPORTER_OTLP_ENDPOINT", "").rstrip("/")
    if not endpoint:
        return

    from opentelemetry import trace
    from opentelemetry.exporter.otlp.proto.http.trace_exporter import OTLPSpanExporter
    from opentelemetry.sdk.resources import Resource
    from opentelemetry.sdk.trace import TracerProvider
    from opentelemetry.sdk.trace.export import BatchSpanProcessor

    if _provider is None:
        _provider = TracerProvider(resource=Resource.create({"service.name": f"orcas-{service_name}"}))
        _provider.add_span_processor(BatchSpanProcessor(
            OTLPSpanExporter(endpoint=f"{endpoint}/v1/traces")
        ))
        trace.set_tracer_provider(_provider)
    if app is not None:
        from opentelemetry.instrumentation.fastapi import FastAPIInstrumentor

        FastAPIInstrumentor.instrument_app(app, tracer_provider=_provider)
    if instrument_httpx and not _httpx_instrumented:
        from opentelemetry.instrumentation.httpx import HTTPXClientInstrumentor

        HTTPXClientInstrumentor().instrument(tracer_provider=_provider)
        _httpx_instrumented = True
