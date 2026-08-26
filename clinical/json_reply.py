"""Recovering a validated object from a model's JSON reply.

Shared by every clinical service that asks the model for structured output. Small free
models occasionally wrap JSON in code fences or pad it with a sentence of prose; the
recovery here is deliberately narrow — strip fences, else slice from the first brace to
the last — and anything beyond that is treated as a failure rather than repaired.

Validation is the Pydantic model's job. If the reply parses but does not match the
schema, that is a failure too: a half-understood clinical suggestion is worse than none.
"""

from __future__ import annotations

import json
import re
from typing import Type, TypeVar

from pydantic import BaseModel, ValidationError

T = TypeVar("T", bound=BaseModel)

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class ReplyError(RuntimeError):
    """The model's reply could not be parsed or failed validation."""


def strip_to_json(text: str) -> str:
    cleaned = _FENCE_RE.sub("", text).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    start, end = cleaned.find("{"), cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def parse_model(raw_text: str, model_cls: Type[T], error_cls: type = ReplyError) -> T:
    """Parse and validate, raising `error_cls` on any failure."""
    candidate = strip_to_json(raw_text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise error_cls(
            f"Model did not return valid JSON: {exc}\n--- raw reply ---\n{raw_text[:1500]}"
        ) from exc
    try:
        return model_cls.model_validate(data)
    except ValidationError as exc:
        raise error_cls(f"Model JSON failed validation:\n{exc}") from exc
