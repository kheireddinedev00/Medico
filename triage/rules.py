"""Loading the rule set, and refusing to run on a broken one.

`data/triage_rules.json` holds every clinical number in this agent. Nothing in
`news2.py`, `red_flags.py` or `decision.py` contains a threshold of its own - which is
the property that makes the rules reviewable by a clinician who does not read Python,
and updatable without a code change.

This module is the gate. A rule set that fails to load raises `RuleSetError` and the
agent does not start. That is deliberate and it is the opposite of how the ICD-10 list
in `clinical/icd10.py` behaves: a missing code list degrades that feature to
shape-checking, because a missing code is an inconvenience. A missing or malformed
triage threshold is a patient sorted wrongly, so there is no degraded mode here.

What gets checked at load, beyond the schema:

- **Bands do not overlap.** Two bands claiming 92 means the score depends on dict order.
- **Bands are contiguous.** A gap means some real observation scores nothing at all,
  silently, and only for patients unlucky enough to land in the hole.
- **Escalation bands cover every aggregate from 0 upward**, with no gap and no overlap.
- **Every priority named anywhere in the file is one of the four.** A typo of "CRITCAL"
  in a red flag must not become a flag that can never fire.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Literal, Optional

from pydantic import BaseModel, ConfigDict, field_validator

from config import TRIAGE_RULES
from triage.schema import Priority

ENGINE_VERSION = "triage-engine-1.0.0"


class RuleSetError(RuntimeError):
    """The rule set is absent, malformed, or internally inconsistent."""


class _Strict(BaseModel):
    model_config = ConfigDict(extra="forbid")


class Band(_Strict):
    """One row of a NEWS2 parameter table. Both bounds inclusive."""

    min: Optional[float] = None
    max: Optional[float] = None
    score: int

    def contains(self, value: float) -> bool:
        if self.min is not None and value < self.min:
            return False
        if self.max is not None and value > self.max:
            return False
        return True


class BandedParameter(_Strict):
    label: str
    unit: str
    bands: list[Band]

    def score_for(self, value: float) -> Optional[int]:
        for band in self.bands:
            if band.contains(value):
                return band.score
        return None


class ValuedParameter(_Strict):
    """A parameter scored from a fixed set of values rather than a numeric range."""

    label: str
    unit: str
    values: dict[str, int]

    def score_for(self, value: str) -> Optional[int]:
        return self.values.get(value)


class News2Source(_Strict):
    title: str
    publisher: str
    year: int
    reference: Optional[str] = None
    url: Optional[str] = None
    licence_note: Optional[str] = None

    def citation(self) -> str:
        return f"{self.publisher} ({self.year}). {self.title}"


class News2Parameters(_Strict):
    """The seven scored parameters, plus the two extra saturation tables Scale 2 needs."""

    respiration_rate: BandedParameter
    spo2_scale_1: BandedParameter
    spo2_scale_2_on_air: BandedParameter
    spo2_scale_2_on_oxygen: BandedParameter
    air_or_oxygen: ValuedParameter
    systolic_bp: BandedParameter
    pulse: BandedParameter
    consciousness: ValuedParameter
    temperature: BandedParameter


class News2Tables(_Strict):
    source: News2Source
    parameters: News2Parameters


class EscalationBand(_Strict):
    min_aggregate: int
    max_aggregate: Optional[int] = None
    priority: Priority
    recommended_action: str

    def contains(self, aggregate: int) -> bool:
        if aggregate < self.min_aggregate:
            return False
        if self.max_aggregate is not None and aggregate > self.max_aggregate:
            return False
        return True


class SingleParameterFloor(_Strict):
    trigger_score: int
    priority: Priority
    reason: str


class PriorityFloor(_Strict):
    priority: Priority


class AiEscalation(_Strict):
    """Where the model's one lever takes a patient, and no further."""

    to_priority: Priority


class Escalation(_Strict):
    bands: list[EscalationBand]
    single_parameter_floor: SingleParameterFloor
    ai_escalation: AiEscalation
    out_of_scope_floor: PriorityFloor
    insufficient_data_floor: PriorityFloor

    def band_for(self, aggregate: int) -> Optional[EscalationBand]:
        for band in self.bands:
            if band.contains(aggregate):
                return band
        return None

    def action_for(self, priority: Priority) -> str:
        """The recommended action for a priority, whatever produced it.

        A red flag can push a patient to CRITICAL with a NEWS2 of 0, so the action
        cannot be looked up by aggregate. It is looked up by the priority actually
        assigned.
        """
        for band in self.bands:
            if band.priority == priority:
                return band.recommended_action
        return "Clinical assessment required."


class Range(_Strict):
    min: float
    max: float

    def contains(self, value: float) -> bool:
        return self.min <= value <= self.max


class UnitSuspicion(_Strict):
    temperature_fahrenheit_range: Range
    weight_pounds_min: float
    height_inches_max: float


class Validation(_Strict):
    plausible: dict[str, Range]
    unit_suspicion: UnitSuspicion
    required_for_full_score: list[str]


class Population(_Strict):
    min_age_years: int
    max_age_years: Optional[int] = None
    excludes_pregnancy: bool = True


class RedFlagRule(_Strict):
    id: str
    floor: Priority
    label: str
    any_of: list[str]
    # When present, the flag fires only if something from `and_any_of` ALSO appears.
    # For context-dependent flags: "chemotherapy" alone is a history, not an emergency;
    # chemotherapy plus a fever is neutropenic sepsis until proven otherwise.
    and_any_of: Optional[list[str]] = None
    reason: str
    source: Optional[str] = None


class RedFlags(_Strict):
    flags: list[RedFlagRule]
    negation_cues: list[str]
    negation_window_chars: int


class WaitingRoom(_Strict):
    tie_break: Literal["arrival", "severity"] = "arrival"
    retriage_interval_minutes: dict[Priority, int]

    def interval_for(self, priority: Priority) -> Optional[int]:
        return self.retriage_interval_minutes.get(priority)


class RuleSet(_Strict):
    ruleset_version: str
    verification_status: str
    population: Population
    news2: News2Tables
    escalation: Escalation
    waiting_room: WaitingRoom
    validation: Validation
    red_flags: RedFlags

    @field_validator("ruleset_version")
    @classmethod
    def _non_empty(cls, value: str) -> str:
        if not value.strip():
            raise ValueError("ruleset_version must not be empty - it is stamped on every result")
        return value

    def is_verified(self) -> bool:
        """Whether a human has checked the thresholds against the cited source."""
        return self.verification_status.strip().upper().startswith("VERIFIED")

    def spo2_parameter(self, scale: int, on_oxygen: Optional[bool]) -> BandedParameter:
        """Pick the saturation table. Scale 2 splits on oxygen; Scale 1 does not."""
        parameters = self.news2.parameters
        if scale != 2:
            return parameters.spo2_scale_1
        return (
            parameters.spo2_scale_2_on_oxygen
            if on_oxygen
            else parameters.spo2_scale_2_on_air
        )


# --------------------------------------------------------------------------------
# Loading
# --------------------------------------------------------------------------------


def _strip_comments(node):
    """Drop every key beginning with an underscore, at every depth.

    The rule file is meant to be read and edited by a person, and notes sitting next to
    the number they explain are worth more than schema purity.
    """
    if isinstance(node, dict):
        return {k: _strip_comments(v) for k, v in node.items() if not k.startswith("_")}
    if isinstance(node, list):
        return [_strip_comments(v) for v in node]
    return node


def _check_bands(name: str, parameter: BandedParameter) -> list[str]:
    """Overlap and contiguity, reported together so one edit fixes everything."""
    problems: list[str] = []
    bands = parameter.bands
    if not bands:
        return [f"{name}: no bands defined"]

    lower_open = [b for b in bands if b.min is None]
    upper_open = [b for b in bands if b.max is None]
    if len(lower_open) != 1:
        problems.append(f"{name}: needs exactly one band unbounded below, found {len(lower_open)}")
    if len(upper_open) != 1:
        problems.append(f"{name}: needs exactly one band unbounded above, found {len(upper_open)}")

    ordered = sorted(bands, key=lambda b: (b.min if b.min is not None else float("-inf")))
    for previous, current in zip(ordered, ordered[1:]):
        if previous.max is None:
            problems.append(f"{name}: the unbounded-above band is not last")
            continue
        if current.min is None:
            continue
        if current.min <= previous.max:
            problems.append(
                f"{name}: bands overlap between {previous.min}-{previous.max} and "
                f"{current.min}-{current.max}"
            )
        # Integer parameters step by 1, temperature by 0.1. Anything wider is a gap.
        elif current.min - previous.max > 1.0001:
            problems.append(
                f"{name}: gap between {previous.max} and {current.min} - an observation "
                f"in that range would score nothing"
            )
    return problems


def _check_escalation(escalation: Escalation) -> list[str]:
    problems: list[str] = []
    bands = sorted(escalation.bands, key=lambda b: b.min_aggregate)
    if not bands:
        return ["escalation: no bands defined"]
    if bands[0].min_aggregate != 0:
        problems.append("escalation: bands must start at aggregate 0")
    if bands[-1].max_aggregate is not None:
        problems.append("escalation: the highest band must be unbounded above")
    for previous, current in zip(bands, bands[1:]):
        if previous.max_aggregate is None:
            problems.append("escalation: an unbounded band is not last")
            continue
        if current.min_aggregate != previous.max_aggregate + 1:
            problems.append(
                f"escalation: aggregate {previous.max_aggregate + 1} is not covered "
                f"exactly once (next band starts at {current.min_aggregate})"
            )
    return problems


def check_consistency(rules: RuleSet) -> list[str]:
    """Every internal problem with a rule set, as a list of sentences.

    Returned rather than raised so `GET /triage/rules` can report them all and a test
    can assert on them individually.
    """
    problems: list[str] = []
    tables = rules.news2.parameters
    for name in (
        "respiration_rate",
        "spo2_scale_1",
        "spo2_scale_2_on_air",
        "spo2_scale_2_on_oxygen",
        "systolic_bp",
        "pulse",
        "temperature",
    ):
        problems += _check_bands(name, getattr(tables, name))

    problems += _check_escalation(rules.escalation)

    if "alert" not in tables.consciousness.values:
        problems.append("consciousness: no score defined for 'alert'")
    if set(tables.air_or_oxygen.values) != {"air", "oxygen"}:
        problems.append("air_or_oxygen: values must be exactly 'air' and 'oxygen'")

    seen: set[str] = set()
    for flag in rules.red_flags.flags:
        if flag.id in seen:
            problems.append(f"red flag id '{flag.id}' is defined more than once")
        seen.add(flag.id)
        if not flag.any_of:
            problems.append(f"red flag '{flag.id}' has no phrases to match")

    known = {p.strip() for p in rules.validation.plausible}
    for field in rules.validation.required_for_full_score:
        if field not in known | {"consciousness"}:
            problems.append(
                f"required_for_full_score names '{field}', which has no plausible range"
            )
    return problems


def load_rules(path: Optional[Path] = None) -> RuleSet:
    """Read, validate and consistency-check the rule set. Raises rather than degrading."""
    source = Path(path or TRIAGE_RULES)
    if not source.exists():
        raise RuleSetError(
            f"No triage rule set at {source}. The agent cannot score a patient without "
            f"one - restore the file rather than running with defaults."
        )
    try:
        raw = json.loads(source.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise RuleSetError(f"{source} is not valid JSON: {exc}") from exc

    try:
        rules = RuleSet.model_validate(_strip_comments(raw))
    except Exception as exc:
        raise RuleSetError(f"{source} does not match the expected format:\n{exc}") from exc

    problems = check_consistency(rules)
    if problems:
        listed = "\n  - ".join(problems)
        raise RuleSetError(f"{source} is internally inconsistent:\n  - {listed}")
    return rules


_CACHED: Optional[RuleSet] = None


def get_rules(path: Optional[Path] = None, refresh: bool = False) -> RuleSet:
    """The process-wide rule set. Cached, because it is read on every triage."""
    global _CACHED
    if refresh or _CACHED is None or path is not None:
        rules = load_rules(path)
        if path is None:
            _CACHED = rules
        return rules
    return _CACHED
