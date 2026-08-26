"""The states a consultation can be in, and the moves between them.

The enum is stored on every visit, so the timeline is meaningful from the first record
onward. The transition table below is what keeps it meaningful: a visit cannot reach
COMPLETED without a working diagnosis having been chosen, and cannot reach
RESULTS_REVIEW without tests having been ordered.

That is enforced here rather than left to the caller because the follow-up visit reads
this timeline. A record that skipped a step today becomes a wrong assessment in three
weeks, and by then nothing distinguishes it from a real one.

The two branches of the encounter, in state terms:

    INITIAL_ASSESSMENT -> ICD10_SELECTION -> TEST_SELECTION -> WAITING_FOR_TESTS
                                          |                          |
                                          |                    RESULTS_REVIEW
                                          v                          v
                                   TREATMENT_SELECTION <-------------+
                                          |
                                    FOLLOW_UP / COMPLETED

Option A is the path through WAITING_FOR_TESTS; option B goes straight from
diagnosis selection to TREATMENT_SELECTION.
"""

from __future__ import annotations

from enum import Enum


class VisitStatus(str, Enum):
    """Stored on every visit. `str` mixin so it serialises as a plain string."""

    INITIAL_ASSESSMENT = "INITIAL_ASSESSMENT"
    ICD10_SELECTION = "ICD10_SELECTION"
    TEST_SELECTION = "TEST_SELECTION"
    WAITING_FOR_TESTS = "WAITING_FOR_TESTS"
    RESULTS_REVIEW = "RESULTS_REVIEW"
    TREATMENT_SELECTION = "TREATMENT_SELECTION"
    FOLLOW_UP = "FOLLOW_UP"
    COMPLETED = "COMPLETED"


# A visit in one of these states is still clinically open: the doctor (or an incoming
# report) is expected to come back to it. Used by the context builder to tell the
# assistant that a previous encounter is unfinished.
OPEN_STATUSES = frozenset(
    {
        VisitStatus.INITIAL_ASSESSMENT,
        VisitStatus.ICD10_SELECTION,
        VisitStatus.TEST_SELECTION,
        VisitStatus.WAITING_FOR_TESTS,
        VisitStatus.RESULTS_REVIEW,
        VisitStatus.TREATMENT_SELECTION,
        VisitStatus.FOLLOW_UP,
    }
)


class InvalidTransition(RuntimeError):
    """Raised when a consultation is asked to move somewhere it cannot go."""


# Which states may follow which. Option A is the branch through TEST_SELECTION;
# Option B goes from the chosen diagnosis straight to TREATMENT_SELECTION.
#
# RESULTS_REVIEW can loop back to TEST_SELECTION on purpose: results that raise a new
# question lead to more tests, and a state machine that could not express that would
# force the doctor to open a second visit for one encounter.
TRANSITIONS: dict[VisitStatus, frozenset[VisitStatus]] = {
    VisitStatus.INITIAL_ASSESSMENT: frozenset({VisitStatus.ICD10_SELECTION}),
    VisitStatus.ICD10_SELECTION: frozenset(
        {VisitStatus.TEST_SELECTION, VisitStatus.TREATMENT_SELECTION}
    ),
    # A doctor who opens the investigation list and then decides against ordering
    # anything must have a way out. Without this the only exit from TEST_SELECTION is
    # to claim tests were ordered when none were.
    VisitStatus.TEST_SELECTION: frozenset(
        {VisitStatus.WAITING_FOR_TESTS, VisitStatus.TREATMENT_SELECTION}
    ),
    VisitStatus.WAITING_FOR_TESTS: frozenset({VisitStatus.RESULTS_REVIEW}),
    VisitStatus.RESULTS_REVIEW: frozenset(
        {VisitStatus.TREATMENT_SELECTION, VisitStatus.TEST_SELECTION}
    ),
    VisitStatus.TREATMENT_SELECTION: frozenset(
        {VisitStatus.FOLLOW_UP, VisitStatus.COMPLETED}
    ),
    VisitStatus.FOLLOW_UP: frozenset({VisitStatus.COMPLETED}),
    # Terminal. A finished encounter is not edited; a new concern is a new visit.
    VisitStatus.COMPLETED: frozenset(),
}


def next_states(current: VisitStatus) -> frozenset[VisitStatus]:
    return TRANSITIONS.get(current, frozenset())


def can_transition(current: VisitStatus, target: VisitStatus) -> bool:
    return target in next_states(current)


def require_transition(current: VisitStatus, target: VisitStatus) -> None:
    """Raise unless the move is allowed."""
    if not can_transition(current, target):
        allowed = ", ".join(sorted(s.value for s in next_states(current))) or "nothing"
        raise InvalidTransition(
            f"A visit at {current.value} cannot move to {target.value}. "
            f"Allowed from here: {allowed}."
        )
