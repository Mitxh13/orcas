"""The worker image does not install FastAPI, but must still register its task."""

import subprocess
import sys


def test_worker_registers_task_without_http_server_dependencies():
    script = """
import builtins
import os

original_import = builtins.__import__

def without_fastapi(name, *args, **kwargs):
    if name.startswith('fastapi') or name.startswith('opentelemetry.instrumentation.fastapi'):
        raise ImportError('FastAPI is absent from the worker image', name='fastapi')
    return original_import(name, *args, **kwargs)

builtins.__import__ = without_fastapi
os.environ['OTEL_EXPORTER_OTLP_ENDPOINT'] = 'http://localhost:4318'

from services.worker.tasks import celery_app

assert celery_app is not None
assert 'orcas.run_complaint' in celery_app.tasks
"""
    result = subprocess.run(
        [sys.executable, "-c", script], capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, result.stdout + result.stderr
