"""The patient triage agent.

A hybrid pipeline, not an autonomous decision maker:

    request -> validation -> NEWS2 -> red flags -> rule floor -> AI -> decision

The deterministic layers decide the priority. The model reads the presentation and may
raise it by one level with a stated reason; it has no way to lower it. Remove the model
entirely and the agent still triages - see `triage/decision.py` for where that is
enforced and `triage/interpret.py` for why it has to be.

`triage.service.triage_payload` is the entire integration surface: a dict in, a dict out.
"""

from triage.schema import Priority, TriageRequest, TriageResult, TriageVitals
from triage.service import TriageInputError, triage, triage_payload, retriage

__all__ = [
    "Priority",
    "TriageRequest",
    "TriageResult",
    "TriageVitals",
    "TriageInputError",
    "triage",
    "triage_payload",
    "retriage",
]
