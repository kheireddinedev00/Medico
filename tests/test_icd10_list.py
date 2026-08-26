"""The optional curated ICD-10 list, and the two modes it switches between."""

from __future__ import annotations

import json

import pytest

from clinical.icd10 import (
    CodeAdvice,
    CodeEntry,
    CodeList,
    CodeListError,
    SuggestedCode,
    candidates_block,
    load_code_list,
    validate_against,
)

ENTRIES = [
    CodeEntry(code="J18.9", description="Pneumonia, unspecified",
              synonyms=["community-acquired pneumonia"]),
    CodeEntry(code="J44.1", description="COPD with (acute) exacerbation"),
    CodeEntry(code="J45.9", description="Asthma, unspecified"),
    CodeEntry(code="U07.1", description="COVID-19, virus identified", synonyms=["COVID"]),
]


def write(tmp_path, payload) -> "Path":
    path = tmp_path / "icd10.json"
    path.write_text(json.dumps(payload), encoding="utf-8")
    return path


# --- loading ---------------------------------------------------------------------


def test_a_missing_file_means_no_list(tmp_path):
    assert load_code_list(tmp_path / "absent.json") is None


def test_an_empty_codes_array_counts_as_no_list(tmp_path):
    """A template with no codes must not switch the system into 'validated' mode."""
    assert load_code_list(write(tmp_path, {"publisher": "WHO", "codes": []})) is None


def test_the_projects_code_list_loads():
    code_list = load_code_list()
    assert code_list is not None, "data/icd10_respiratory.json should be populated"
    assert len(code_list) > 250
    assert "World Health Organization" in code_list.provenance


@pytest.mark.parametrize(
    "code,expected",
    [
        ("J18.9", "Pneumonia, unspecified"),
        ("J44.1", "Chronic obstructive pulmonary disease with acute exacerbation, unspecified"),
        ("J45.9", "Asthma, unspecified"),
        ("J20.9", "Acute bronchitis, unspecified"),
        ("J90", "Pleural effusion, not elsewhere classified"),
    ],
)
def test_the_common_respiratory_codes_are_present(code, expected):
    assert load_code_list().lookup(code).description == expected


@pytest.mark.parametrize("code", ["A15", "A16.2", "U07.1", "C34", "I26", "R05", "R06.0"])
def test_the_codes_outside_chapter_j_are_present(code):
    """The J00-J99 export lacks these; the assistant raises all of them."""
    assert load_code_list().lookup(code) is not None


def test_block_headings_did_not_survive_the_import():
    """'J00-J06' is a heading, not a diagnosis code."""
    codes = {e.code for e in load_code_list().codes}
    assert not any("-" in code for code in codes)


def test_no_duplicate_codes():
    codes = [e.code for e in load_code_list().codes]
    assert len(codes) == len(set(codes))


def test_a_populated_file_loads(tmp_path):
    path = write(tmp_path, {
        "publisher": "World Health Organization",
        "edition": "ICD-10 Version:2019",
        "codes": [{"code": "J18.9", "description": "Pneumonia, unspecified"}],
    })
    code_list = load_code_list(path)

    assert len(code_list) == 1
    assert "World Health Organization" in code_list.provenance
    assert code_list.lookup("j18.9").description == "Pneumonia, unspecified"


def test_comment_keys_are_allowed(tmp_path):
    path = write(tmp_path, {
        "_note": "hand-edited",
        "_todo": "add TB codes",
        "codes": [{"code": "J18.9", "description": "Pneumonia"}],
    })
    assert len(load_code_list(path)) == 1


def test_malformed_json_is_reported_clearly(tmp_path):
    path = tmp_path / "icd10.json"
    path.write_text("{oops", encoding="utf-8")
    with pytest.raises(CodeListError, match="not valid JSON"):
        load_code_list(path)


def test_an_entry_that_is_not_a_code_is_rejected(tmp_path):
    path = write(tmp_path, {"codes": [{"code": "PNEUMONIA", "description": "x"}]})
    with pytest.raises(CodeListError, match="not ICD-10 codes"):
        load_code_list(path)


def test_a_missing_description_is_rejected(tmp_path):
    path = write(tmp_path, {"codes": [{"code": "J18.9"}]})
    with pytest.raises(CodeListError):
        load_code_list(path)


# --- validation ------------------------------------------------------------------


def test_without_a_list_only_shape_is_checked():
    advice = CodeAdvice(codes=[
        SuggestedCode(code="J18.9", description="model's wording"),
        SuggestedCode(code="J99.99999"),
        SuggestedCode(code="nonsense"),
    ])
    result = validate_against(advice, None)

    assert [c.code for c in result.codes] == ["J18.9"]
    assert result.codes[0].description == "model's wording"


def test_with_a_list_unknown_codes_are_dropped():
    advice = CodeAdvice(codes=[
        SuggestedCode(code="J18.9"),
        SuggestedCode(code="J17.3", description="a plausible invention"),
    ])
    result = validate_against(advice, CodeList(codes=ENTRIES))

    assert [c.code for c in result.codes] == ["J18.9"]
    assert any("J17.3" in note for note in result.notes)


def test_the_description_comes_from_the_list_not_the_model():
    advice = CodeAdvice(codes=[
        SuggestedCode(code="J44.1", description="Chronic bronchitis, mild",
                      rationale="fits this presentation")
    ])
    result = validate_against(advice, CodeList(codes=ENTRIES))

    assert result.codes[0].description == "COPD with (acute) exacerbation"
    # The rationale is about this patient, so it survives.
    assert result.codes[0].rationale == "fits this presentation"


def test_lowercase_suggestions_are_matched_and_normalised():
    result = validate_against(
        CodeAdvice(codes=[SuggestedCode(code="j45.9")]), CodeList(codes=ENTRIES)
    )
    assert [c.code for c in result.codes] == ["J45.9"]


# --- prompt injection ------------------------------------------------------------


def test_no_list_means_no_permitted_block():
    assert candidates_block(None, "Pneumonia") is None


def test_a_short_list_is_sent_whole():
    block = candidates_block(CodeList(codes=ENTRIES), "Community-acquired pneumonia")
    for entry in ENTRIES:
        assert entry.code in block
    assert "Choose ONLY from this list" in block


def test_a_long_list_is_filtered_by_the_diagnosis():
    many = ENTRIES + [
        CodeEntry(code=f"J{n:02d}.0", description=f"Unrelated condition {n}")
        for n in range(10, 80)
    ]
    block = candidates_block(CodeList(codes=many), "asthma exacerbation")

    assert "J45.9" in block
    assert block.count("\n") < len(many)


def test_synonyms_help_matching():
    many = ENTRIES + [
        CodeEntry(code=f"A{n:02d}.0", description=f"Filler {n}") for n in range(10, 80)
    ]
    block = candidates_block(CodeList(codes=many), "suspected COVID infection")
    assert "U07.1" in block
