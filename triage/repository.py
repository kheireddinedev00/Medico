"""What the triage agent needs from a store, stated as a protocol.

The mirror of `storage/repository.py`, and here rather than there for one reason: the
dependency has to run one way. `storage/triage_repository.py` imports this module and
satisfies it; nothing in `triage/` imports `storage`. That is what keeps the agent
usable from the stateless HTTP service, which has no database behind it at all.

The protocol is optional in the strictest sense: nothing in the triage pipeline takes a
repository. `triage.service.triage` returns a result and writes nowhere, exactly as
`chatbot.consultation.assess` does. Recording is a separate act by the caller, which is
what makes the agent safe to run speculatively - a triage that is never acted on leaves
no record behind.
"""

from __future__ import annotations

from typing import Optional, Protocol

from triage.queue import WaitingRoomEntry
from triage.schema import TriageResult


class TriageRepository(Protocol):
    """The triage audit trail and the current waiting room.

    One protocol rather than two, because the two are written together: admitting a
    patient records a decision and puts them in the room, and an implementation against
    a backend's own tables should not have to keep the pair consistent across two
    adapters.
    """

    def save_result(self, result: TriageResult) -> None:
        """Append a triage decision. Never updates an existing one."""
        ...

    def get_result(self, request_id: str) -> Optional[TriageResult]:
        ...

    def list_for_patient(
        self, patient_id: str, limit: Optional[int] = None
    ) -> list[TriageResult]:
        """That patient's triage decisions, newest first."""
        ...

    def save_entry(self, key: str, entry: WaitingRoomEntry) -> None:
        """Insert or replace one waiting-room entry."""
        ...

    def load_room(self, include_removed: bool = False) -> dict[str, WaitingRoomEntry]:
        """The waiting room, keyed as it was saved."""
        ...
