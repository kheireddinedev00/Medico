"""The waiting room: who is next, and who has been waiting too long to still be trusted.

A triage result is a statement about one patient at one moment. A waiting room is the
set of those statements, ordered, going stale at different rates. This module holds the
second thing, and it holds it in memory - persistence is `storage/triage_repository.py`,
and the ordering rules do not depend on where the entries came from.

Four behaviours, each of which is a decision rather than an implementation detail:

**Ordering.** Priority first, arrival time second. Not NEWS2 within a priority: see the
rationale in the rule file. Ordering is total and deterministic, so the same waiting room
always renders in the same order and a nurse's screen does not shuffle under them.

**Staleness.** Observations expire. A STANDARD patient triaged ninety minutes ago is not
a STANDARD patient; they are a patient nobody has looked at since. `needs_retriage()`
says so, and the interval per priority comes from the rule set.

**Deterioration.** Re-triage replaces the result and keeps the arrival time, so a patient
who deteriorates moves up the queue but does not lose their place within their new
priority. The deterioration note comes from `service.retriage`.

**Override.** A clinician's decision is recorded beside the AI's, never over it. The
queue sorts on the override; the result keeps what the system said, so "the clinician
disagreed" is answerable from the record. This mirrors how `patient.visit` keeps
`differential` separate from `working_diagnosis`.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Iterator, Optional

from pydantic import BaseModel, ConfigDict, Field

from triage.rules import RuleSet, get_rules
from triage.schema import RANK, ClinicianOverride, Priority, TriageResult


def _now() -> datetime:
    return datetime.now(timezone.utc)


class WaitingRoomEntry(BaseModel):
    """One patient waiting, with their most recent triage result."""

    model_config = ConfigDict(extra="forbid")

    result: TriageResult
    display_name: Optional[str] = None
    arrived_at: datetime = Field(default_factory=_now)
    triaged_at: datetime = Field(default_factory=_now)
    # Every result this patient has had, oldest first, including the current one. This
    # is the deterioration trail and it is never pruned.
    history: list[TriageResult] = []
    removed_at: Optional[datetime] = None
    removal_reason: Optional[str] = None

    @property
    def patient_id(self) -> Optional[str]:
        return self.result.patient_id

    @property
    def priority(self) -> Priority:
        return self.result.effective_priority()

    def waiting_minutes(self, now: Optional[datetime] = None) -> float:
        return ((now or _now()) - self.arrived_at).total_seconds() / 60

    def needs_retriage(self, rules: RuleSet, now: Optional[datetime] = None) -> bool:
        """Whether these observations are too old to sort on."""
        interval = rules.waiting_room.interval_for(self.priority)
        if interval is None:
            return False
        if interval == 0:
            # CRITICAL: always flagged. This patient should not be in the room at all.
            return True
        return (now or _now()) - self.triaged_at >= timedelta(minutes=interval)

    def deteriorating(self) -> bool:
        """True when the latest priority is more urgent than the one before it."""
        if len(self.history) < 2:
            return False
        return RANK[self.history[-1].priority] > RANK[self.history[-2].priority]


class WaitingRoom(BaseModel):
    """An ordered set of waiting patients. In-memory; persistence lives elsewhere."""

    model_config = ConfigDict(extra="forbid")

    entries: dict[str, WaitingRoomEntry] = {}

    # --- mutation ----------------------------------------------------------------

    def admit(
        self,
        result: TriageResult,
        display_name: Optional[str] = None,
        arrived_at: Optional[datetime] = None,
    ) -> WaitingRoomEntry:
        """Add a newly triaged patient."""
        entry = WaitingRoomEntry(
            result=result,
            display_name=display_name,
            arrived_at=arrived_at or result.created_at,
            triaged_at=result.created_at,
            history=[result],
        )
        self.entries[entry.result.request_id] = entry
        return entry

    def update(self, key: str, result: TriageResult) -> WaitingRoomEntry:
        """Replace an entry's result after a re-triage, keeping its arrival time.

        Keyed by the ORIGINAL request id, so a re-triage does not create a second
        entry for the same person and the queue cannot show one patient twice.
        """
        entry = self.entries.get(key)
        if entry is None:
            raise KeyError(f"No waiting-room entry {key}")
        entry.result = result
        entry.triaged_at = result.created_at
        entry.history.append(result)
        return entry

    def override(
        self,
        key: str,
        priority: Priority,
        reason: str,
        clinician: Optional[str] = None,
    ) -> WaitingRoomEntry:
        """Record a clinician's decision. The AI's result is left intact beneath it."""
        entry = self.entries.get(key)
        if entry is None:
            raise KeyError(f"No waiting-room entry {key}")
        if not reason.strip():
            raise ValueError(
                "An override needs a reason. It is the only record of why the system "
                "was disagreed with, and it is what makes the disagreement reviewable."
            )
        entry.result.override = ClinicianOverride(
            priority=priority, reason=reason.strip(), clinician=clinician
        )
        return entry

    def remove(self, key: str, reason: str = "seen by clinician") -> Optional[WaitingRoomEntry]:
        """Take a patient out of the queue, keeping the entry for the audit trail."""
        entry = self.entries.get(key)
        if entry is None:
            return None
        entry.removed_at = _now()
        entry.removal_reason = reason
        return entry

    # --- reading -----------------------------------------------------------------

    def waiting(self) -> list[WaitingRoomEntry]:
        return [e for e in self.entries.values() if e.removed_at is None]

    def ordered(self, rules: Optional[RuleSet] = None) -> list[WaitingRoomEntry]:
        """The queue, most urgent first. Total and deterministic."""
        rules = rules or get_rules()
        by_severity = rules.waiting_room.tie_break == "severity"

        def key(entry: WaitingRoomEntry):
            aggregate = entry.result.news2.aggregate if entry.result.news2 else 0
            return (
                -RANK[entry.priority],
                -aggregate if by_severity else 0,
                entry.arrived_at,
                # Final tie-break so the order never depends on dict insertion.
                entry.result.request_id,
            )

        return sorted(self.waiting(), key=key)

    def next_patient(self, rules: Optional[RuleSet] = None) -> Optional[WaitingRoomEntry]:
        queue = self.ordered(rules)
        return queue[0] if queue else None

    def stale(
        self, rules: Optional[RuleSet] = None, now: Optional[datetime] = None
    ) -> list[WaitingRoomEntry]:
        """Everyone whose observations are older than their priority allows."""
        rules = rules or get_rules()
        return [e for e in self.ordered(rules) if e.needs_retriage(rules, now)]

    def counts(self) -> dict[Priority, int]:
        counts = {priority: 0 for priority in Priority}
        for entry in self.waiting():
            counts[entry.priority] += 1
        return counts

    def __len__(self) -> int:
        return len(self.waiting())

    def __iter__(self) -> Iterator[WaitingRoomEntry]:  # type: ignore[override]
        return iter(self.ordered())
