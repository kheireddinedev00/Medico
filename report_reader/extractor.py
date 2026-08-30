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


def _repair_json(text: str) -> str:
    """Fix the punctuation slips a model makes, and nothing else.

    Two are common enough to be worth handling, and both are unambiguous — the repair can
    only produce the document the model was plainly trying to write:

    - a trailing comma before a closing brace or bracket;
    - a missing comma between one value and the next key, which is what
      "Expecting ',' delimiter" almost always means.

    Nothing here invents or alters a *value*. A repair that guessed at content would be
    fabricating clinical data, which is worse than failing — so the result is re-parsed and
    re-validated by the caller, and discarded if it does not hold up.
    """
    # A trailing comma: {"a": 1,} or [1, 2,]
    repaired = re.sub(r",(\s*[}\]])", r"\1", text)

    # A missing comma between a finished value and the next key on a following line. The
    # lookbehind is restricted to the characters a value can legally end with, so this
    # cannot fire inside a string.
    repaired = re.sub(
        r'((?:"|\d|\]|\}|e|l))\s*\n(\s*")',
        r"\1,\n\2",
        repaired,
    )
    # That rule over-fires where a comma already exists; collapse any it doubled.
    return re.sub(r",\s*,", ",", repaired)


def parse_and_validate(raw_text: str) -> ExtractedReport:
    candidate = _strip_to_json(raw_text)
    try:
        data = json.loads(candidate)
    except json.JSONDecodeError as first_error:
        # One repair attempt before giving up. A whole page of transcription is worth more
        # than a missing comma, and the physician's alternative is retyping the report.
        try:
            data = json.loads(_repair_json(candidate))
        except json.JSONDecodeError:
            raise ExtractionError(
                f"Model did not return valid JSON: {first_error}\n"
                f"--- raw output ---\n{raw_text[:4000]}"
            ) from first_error
    try:
        return ExtractedReport.model_validate(data)
    except ValidationError as exc:
        raise ExtractionError(
            f"Model JSON failed schema validation:\n{exc}\n--- parsed ---\n"
            f"{json.dumps(data, indent=2)[:2000]}"
        ) from exc


# One corrective retry, for the same reason the assessment path has one: a small free model
# occasionally drops a comma or wraps its reply in prose, and a single reminder recovers most
# of those. Without it, a whole page of transcription is thrown away over a punctuation slip
# and the physician is told to upload the document again.
#
# The cost is real — the images are re-sent, so a retry is another full model call — which is
# why there is exactly one. Beyond that the failure is not a slip, and the raw reply is
# surfaced instead of being retried into a different kind of wrong.
_RETRY_NOTE = (
    "Your previous reply was not a single valid JSON object matching the required schema. "
    "Return ONLY the JSON object: no markdown fences, no commentary, every key present."
)


def extract_report(
    path: str | Path,
    client: VLMClient | None = None,
    attempts: int = 2,
) -> dict:
    """Run the full pipeline on a file and return a validated dict."""
    path = Path(path)
    if not path.exists():
        raise FileNotFoundError(path)

    client = client or OpenRouterVLMClient()

    images = preprocess_images(load_images(path))

    failure: ExtractionError | None = None
    for attempt in range(max(1, attempts)):
        prompt = SYSTEM_PROMPT if attempt == 0 else f"{SYSTEM_PROMPT}\n\n{_RETRY_NOTE}"
        raw_text = client.extract(prompt, images)
        try:
            report = parse_and_validate(raw_text)
            # mode="json" keeps the output JSON-native (no Python objects) downstream.
            return report.model_dump(mode="json")
        except ExtractionError as exc:
            failure = exc

    raise failure
