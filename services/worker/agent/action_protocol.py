"""
JSON Action Protocol (JAP) – fallback for models without native tool-calling.

When free-tier LLMs don't support function-calling, the model is asked to emit
a JSON blob like:

    {"action": "search_corpus", "args": {"query": "...", "k": 3}}

This module provides a Pydantic validator that parses and validates these
action payloads, ensuring the agent never executes an invalid or fabricated tool.
"""

from typing import Any, Dict, List, Optional, Union
from pydantic import BaseModel, Field, ValidationError
import json
import logging
import re

logger = logging.getLogger("worker.action_protocol")


# --- Pydantic action models ---

class SearchCorpusArgs(BaseModel):
    query: str
    framework: Optional[str] = None
    entity_type: Optional[str] = None
    k: int = Field(default=3, ge=1, le=10)


class GetClauseArgs(BaseModel):
    doc_id: str
    clause_id: str


class GetComplaintFieldsArgs(BaseModel):
    fields: List[str]


class ListFrameworkScopeArgs(BaseModel):
    framework: str


ACTION_REGISTRY: Dict[str, type] = {
    "search_corpus": SearchCorpusArgs,
    "get_clause": GetClauseArgs,
    "get_complaint_fields": GetComplaintFieldsArgs,
    "list_framework_scope": ListFrameworkScopeArgs,
}


class ActionEnvelope(BaseModel):
    """Top-level envelope the model must emit when using JSON action protocol."""
    action: str
    args: Dict[str, Any] = Field(default_factory=dict)


def parse_action(raw_text: str) -> Optional[Dict[str, Any]]:
    """
    Parse a JSON action from raw model output text.

    Returns validated {"action": str, "args": dict} or None on failure.
    Tries direct parse first, then falls back to regex JSON extraction.
    """
    blob = None
    # Try direct parse
    try:
        blob = json.loads(raw_text.strip())
    except (json.JSONDecodeError, ValueError):
        # Try regex extraction
        match = re.search(r"\{.*\}", raw_text, re.DOTALL)
        if match:
            try:
                blob = json.loads(match.group(0))
            except (json.JSONDecodeError, ValueError):
                pass

    if not blob or not isinstance(blob, dict):
        logger.warning("Could not extract JSON from model output.")
        return None

    # Validate envelope
    try:
        envelope = ActionEnvelope(**blob)
    except ValidationError as e:
        logger.warning("Action envelope validation failed: %s", e)
        return None

    # Validate action name
    if envelope.action not in ACTION_REGISTRY:
        logger.warning("Unknown action '%s'. Known: %s", envelope.action, list(ACTION_REGISTRY.keys()))
        return None

    # Validate action-specific args
    args_model = ACTION_REGISTRY[envelope.action]
    try:
        validated_args = args_model(**envelope.args)
    except ValidationError as e:
        logger.warning("Action args validation failed for '%s': %s", envelope.action, e)
        return None

    return {
        "action": envelope.action,
        "args": validated_args.model_dump()
    }
