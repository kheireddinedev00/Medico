"""Presentational red flags: what the patient said, matched against a reviewed list.

NEWS2 scores physiology. The patient with crushing central chest pain, a normal pulse
and a normal blood pressure scores 0 - and is having a myocardial infarction. This layer
exists for exactly that patient, and it is why the agent cannot be a scoring function
alone.

The matching is substring matching, and that is a considered choice rather than a
shortcut. This layer is the safety net under the model: it has to work when OpenRouter
is down, has to behave identically every time it sees the same words, and has to be
reviewable by a clinician who wants to know what the system will and will not catch.
A phrase list satisfies all three. It also, unavoidably, misses paraphrases - which is
precisely the gap `triage/interpret.py` fills, and why the model is allowed to add
flags but never to remove one.

Negation is handled crudely and conservatively. "no chest pain" does not fire the
cardiac flag; "no fever but severe chest pain" still does, because negation is scoped to
a short window BEFORE the phrase rather than to the whole sentence. When the two
interpretations conflict, this layer fires. A false positive costs a clinician thirty
seconds; a false negative costs something else entirely.

The known limitation of that approach: negation that follows the phrase ("chest pain
ruled out last week") is not detected, so it fires. Widening the window to catch it
reintroduces the failure the window was narrowed to fix, and doing it properly needs
clause parsing. The over-triage is accepted and is covered by a test that says so.
"""

from __future__ import annotations

import re
from typing import Iterator, Optional

from triage.rules import RedFlagRule, RuleSet
from triage.schema import Priority, RedFlagHit, TriageRequest

# Collapse runs of whitespace and punctuation so "chest  pain," matches "chest pain".
_NORMALISE_RE = re.compile(r"[\s ]+")


def normalise(text: str) -> str:
    return _NORMALISE_RE.sub(" ", text.lower()).strip()


def _occurrences(haystack: str, needle: str) -> Iterator[int]:
    start = haystack.find(needle)
    while start != -1:
        yield start
        start = haystack.find(needle, start + 1)


def is_negated(haystack: str, position: int, cues: list[str], window: int) -> bool:
    """Whether a negation cue appears in the short window before `position`.

    The window is short on purpose. A long one turns "denies alcohol use, reports severe
    chest pain" into a negation of the chest pain.
    """
    start = max(0, position - window)
    preceding = haystack[start:position]
    return any(cue in preceding for cue in cues)


def find_phrase(text: str, phrase: str, cues: list[str], window: int) -> Optional[str]:
    """The first non-negated occurrence of `phrase`, or None.

    Returns the phrase itself so the caller can show the clinician what was matched.
    """
    target = normalise(phrase)
    for position in _occurrences(text, target):
        if not is_negated(text, position, cues, window):
            return target
    return None


def _fires(text: str, flag: RedFlagRule, cues: list[str], window: int) -> Optional[str]:
    matched = None
    for phrase in flag.any_of:
        matched = find_phrase(text, phrase, cues, window)
        if matched:
            break
    if not matched:
        return None

    if flag.and_any_of:
        context = None
        for phrase in flag.and_any_of:
            context = find_phrase(text, phrase, cues, window)
            if context:
                break
        if not context:
            return None
        return f"{matched} + {context}"
    return matched


def detect(request: TriageRequest, rules: RuleSet) -> list[RedFlagHit]:
    """Every red flag the presentation fires, most urgent first."""
    config = rules.red_flags
    text = normalise(request.presentation_text())
    if not text:
        return []

    # normalise() strips the trailing space the cues carry in the rule file, and that
    # space is load-bearing: without it "no " matches inside "nose" and "not " inside
    # "notes". It is put back here rather than relying on the file being edited carefully.
    cues = [normalise(cue) + " " for cue in config.negation_cues]
    window = config.negation_window_chars

    hits: list[RedFlagHit] = []
    for flag in config.flags:
        matched = _fires(text, flag, cues, window)
        if matched is None:
            continue
        hits.append(
            RedFlagHit(
                id=flag.id,
                label=flag.label,
                floor=flag.floor,
                matched=matched,
                reason=flag.reason,
                source=flag.source,
                detected_by="rules",
            )
        )

    order = {Priority.CRITICAL: 0, Priority.URGENT: 1, Priority.STANDARD: 2, Priority.LOW: 3}
    hits.sort(key=lambda h: order[h.floor])
    return hits


def floor_from(hits: list[RedFlagHit]) -> Optional[Priority]:
    """The most urgent floor any fired flag demands, or None if none fired."""
    if not hits:
        return None
    order = {Priority.LOW: 0, Priority.STANDARD: 1, Priority.URGENT: 2, Priority.CRITICAL: 3}
    return max((h.floor for h in hits), key=lambda p: order[p])
