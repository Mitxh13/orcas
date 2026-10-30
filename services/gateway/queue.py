"""Celery dispatch used by the intake API."""

from celery import Celery
from opentelemetry import propagate, trace

from libs.orcas_common.config import celery_broker_url, celery_result_backend


class CeleryDispatcher:
    def __init__(self) -> None:
        self.app = Celery("orcas-gateway", broker=celery_broker_url(), backend=celery_result_backend())

    def send(self, run_id: str) -> None:
        with trace.get_tracer(__name__).start_as_current_span("queue.publish"):
            headers = {"correlation_id": run_id}
            propagate.inject(headers)
            self.app.send_task("orcas.run_complaint", args=[run_id], queue="orcas_tasks", task_id=run_id,
                               headers=headers)

    def ping(self) -> bool:
        with self.app.connection_for_write() as connection:
            connection.ensure_connection(max_retries=1)
        return True
