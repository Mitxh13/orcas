"""Gateway request model aligned with complaint.v1.schema.json."""

from __future__ import annotations

from datetime import date, datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


class Complaint(BaseModel):
    model_config = ConfigDict(extra="forbid")

    client_id: str = Field(min_length=3, max_length=64, pattern=r"^[A-Za-z0-9_-]+$")
    is_synthetic: Literal[True]
    channel: Literal["web", "email", "phone", "branch"]
    text: str = Field(min_length=30, max_length=4000)
    date_of_event: date
    date_filed: date
    amount_disputed_inr: float | None = Field(default=None, ge=0, le=1_000_000_000)
    entity_name_hint: str | None = Field(default=None, max_length=120)
    prior_complaint_to_entity: bool | None = None
    prior_complaint_date: date | None = None

    @field_validator("text", "entity_name_hint")
    @classmethod
    def printable_text(cls, value: str | None) -> str | None:
        if value is not None and not value.isprintable():
            raise ValueError("control characters are not allowed")
        return value


class SubmitResponse(BaseModel):
    run_id: str
    status_url: str


class RunStatus(BaseModel):
    run_id: str
    status: Literal["queued", "running", "completed", "escalated", "failed"]
    submitted_at: datetime
    started_at: datetime | None = None
    finished_at: datetime | None = None
    result: dict[str, Any] | None = None


class FeedbackRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")

    rating: Literal["up", "down"]
    reason: str = Field(default="", max_length=500)


def cross_field_errors(complaint: Complaint, today: date | None = None) -> list[dict[str, Any]]:
    today = today or date.today()
    errors: list[dict[str, Any]] = []

    def add(field: str, message: str) -> None:
        errors.append({"field": field, "message": message, "type": "value_error"})

    if complaint.date_of_event > today:
        add("date_of_event", "must not be in the future")
    if complaint.date_filed > today:
        add("date_filed", "must not be in the future")
    if complaint.date_filed < complaint.date_of_event:
        add("date_filed", "must be on or after date_of_event")
    if complaint.prior_complaint_to_entity:
        if complaint.prior_complaint_date is None:
            add("prior_complaint_date", "required when prior_complaint_to_entity is true")
        elif not complaint.date_of_event <= complaint.prior_complaint_date <= complaint.date_filed:
            add("prior_complaint_date", "must be between date_of_event and date_filed")
    return errors
