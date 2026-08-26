"""Top-level orchestration for the report reader.

    file path -> load images -> preprocess -> VLM -> parse JSON -> validate schema -> dict

The public entry point is `extract_report(path)`, which returns a clean, schema-validated
dict ready for another system to consume.
"""

from __future__ import annotations

import json
import re
from pathlib import Path

from pydantic import ValidationError

from report_reader.loader import load_images
from report_reader.preprocess import preprocess_images
from report_reader.prompt import SYSTEM_PROMPT
from report_reader.schema import ExtractedReport
from report_reader.vlm_client import OpenRouterVLMClient, VLMClient

_FENCE_RE = re.compile(r"^\s*```(?:json)?\s*|\s*```\s*$", re.IGNORECASE)


class ExtractionError(RuntimeError):
    """Raised when the model output cannot be parsed or validated."""


def _strip_to_json(text: str) -> str:
    """Best-effort recovery of a JSON object from the model's raw text.

    The prompt forbids markdown, but models occasionally wrap output in ``` fences or
    add stray prose. We strip fences and, failing that, slice from the first '{' to the
    last '}'.
    """
    cleaned = _FENCE_RE.sub("", text).strip()
    if cleaned.startswith("{") and cleaned.endswith("}"):
        return cleaned
    start = cleaned.find("{")
    end = cleaned.rfind("}")
    if start != -1 and end != -1 and end > start:
        return cleaned[start : end + 1]
    return cleaned


def parse_and_validate(raw_text: str) -> ExtractedReport:
    candidate = _strip_to_json(raw_text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as exc:
        raise ExtractionError(
            f"Model did not return valid JSON: {exc}\n--- raw output ---\n{raw_text[:2000]}"
        ) from exc
    try:
        return ExtractedReport.model_validate(data)
    except ValidationError as exc:
        raise ExtractionError(
            f"Model JSON failed schema validation:\n{exc}\n--- parsed ---\n"
            f"{json.dumps(data, indent=2)[:2000]}"
        ) from exc


def extract_report(path: str | Path, client: VLMClient | None = None) -> dict:
    """Run the full pipeline on a file and return a validated dict."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    client = client or OpenRouterVLMClient()

    images = preprocess_images(load_images(path))
    raw_text = client.extract(SYSTEM_PROMPT, images)
    report = parse_and_validate(raw_text)
    # mode="json" keeps the output JSON-native (no Python objects) for downstream use.
    return report.model_dump(mode="json")
