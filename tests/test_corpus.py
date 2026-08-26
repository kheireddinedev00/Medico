"""Phase 3: curation of the reference library and condition tagging."""

from __future__ import annotations

import json

import pytest
from langchain_core.documents import Document

from clinical.differential import EvidenceSource
from rag.ingest import curate, tag_chunks
from rag.respiratory_corpus import (
    ManifestError,
    Section,
    SourceSpec,
    conditions_to_metadata,
    load_manifest,
    tag_conditions,
)

WHO_FILE = "WHO_HTM_TB_2008.410_eng.pdf"


@pytest.fixture
def spec():
    return SourceSpec(
        file="guide.pdf",
        title="A Guide",
        publisher="WHO",
        year=2008,
        reference="WHO/X/1",
        sections=[
            Section(name="Chapter 4 - Clinical", first_page=52, last_page=69),
            Section(name="Chapter 5 - Counselling", first_page=70, last_page=75),
        ],
    )


def page(source="guide.pdf", index=0, text="text") -> Document:
    """A loaded PDF page. PyPDFLoader numbers `page` from zero."""
    return Document(
        page_content=text,
        metadata={"source": f"data/references/{source}", "page": index, "page_label": str(index + 1)},
    )


# --- condition tagging -----------------------------------------------------------


@pytest.mark.parametrize(
    "text,expected",
    [
        ("Patients with an asthma exacerbation", "asthma"),
        ("known COPD without wheeze", "copd"),
        ("signs suggesting non-severe pneumonia", "pneumonia"),
        ("screening for pulmonary tuberculosis", "tuberculosis"),
        ("suspected TB cases are registered", "tuberculosis"),
        ("acute bronchitis and other infections", "acute_bronchitis"),
        ("could indicate pulmonary thromboembolism", "pulmonary_embolism"),
        ("upper respiratory tract involvement", "upper_respiratory_infection"),
    ],
)
def test_conditions_are_detected(text, expected):
    assert expected in tag_conditions(text)


def test_a_chunk_can_carry_several_conditions():
    tags = tag_conditions("Wheeze occurs in asthma and in many COPD exacerbations.")
    assert "asthma" in tags and "copd" in tags


def test_untagged_text_returns_nothing():
    assert tag_conditions("The national working group should meet quarterly.") == []


def test_word_boundaries_prevent_false_matches():
    # "Haemophilus influenzae" is a bacterium, not the flu.
    assert "influenza" not in tag_conditions("caused by Haemophilus influenzae")
    # "TB" must not match inside another word.
    assert "tuberculosis" not in tag_conditions("the subtle distinction")


def test_metadata_encoding_is_delimited_for_safe_matching():
    assert conditions_to_metadata(["asthma", "copd"]) == ",asthma,copd,"
    assert conditions_to_metadata([]) == ""
    # The delimiters stop a prefix from matching a different tag.
    assert ",asthma," in conditions_to_metadata(["asthma"])
    assert ",asth," not in conditions_to_metadata(["asthma"])


# --- the manifest ----------------------------------------------------------------


def test_section_lookup_uses_inclusive_page_ranges(spec):
    assert spec.section_for(52).name.startswith("Chapter 4")
    assert spec.section_for(69).name.startswith("Chapter 4")
    assert spec.section_for(70).name.startswith("Chapter 5")
    assert spec.section_for(51) is None
    assert spec.section_for(76) is None


def test_citation_reads_like_a_reference(spec):
    assert spec.citation == "WHO, 2008. A Guide (WHO/X/1)"


def test_declared_page_count(spec):
    assert spec.total_pages_declared() == 24


def test_real_manifest_declares_the_who_source():
    manifest = load_manifest()
    assert WHO_FILE in manifest
    assert manifest[WHO_FILE].total_pages_declared() == 24


def test_missing_manifest_is_an_error(tmp_path):
    with pytest.raises(ManifestError):
        load_manifest(tmp_path / "nope.json")


def test_malformed_manifest_is_an_error(tmp_path):
    bad = tmp_path / "sources.json"
    bad.write_text("{not json", encoding="utf-8")
    with pytest.raises(ManifestError):
        load_manifest(bad)


def test_unknown_manifest_key_is_rejected(tmp_path):
    bad = tmp_path / "sources.json"
    bad.write_text(json.dumps({"sources": [{"file": "a.pdf", "title": "T", "oops": 1}]}))
    with pytest.raises(Exception):
        load_manifest(bad)


# --- curation --------------------------------------------------------------------


def test_only_declared_pages_survive(spec):
    manifest = {"guide.pdf": spec}
    # PDF pages 51, 52 and 76 -> loader indices 50, 51 and 75.
    pages = [page(index=50), page(index=51), page(index=75)]
    kept, report = curate(pages, manifest)

    assert len(kept) == 1
    assert kept[0].metadata["pdf_page"] == 52
    assert report.dropped["guide.pdf"] == 2


def test_kept_pages_are_stamped_with_provenance(spec):
    kept, _ = curate([page(index=51)], {"guide.pdf": spec})
    meta = kept[0].metadata

    assert meta["citation"] == "WHO, 2008. A Guide (WHO/X/1)"
    assert meta["section"] == "Chapter 4 - Clinical"
    assert meta["source_name"] == "guide.pdf"
    assert meta["pdf_page"] == 52


def test_undeclared_files_are_skipped_entirely(spec):
    kept, report = curate([page(source="random.pdf", index=51)], {"guide.pdf": spec})

    assert kept == []
    assert "random.pdf" in report.undeclared


def test_chunks_are_tagged_with_their_conditions():
    chunks = [
        Document(page_content="asthma exacerbation", metadata={}),
        Document(page_content="quarterly supervision visits", metadata={}),
    ]
    histogram = tag_chunks(chunks)

    assert chunks[0].metadata["conditions"] == ",asthma,"
    assert chunks[1].metadata["conditions"] == ""
    assert histogram["asthma"] == 1
    assert histogram["(untagged)"] == 1


# --- how a citation is shown to the physician ------------------------------------


def test_reference_prefers_the_curated_citation():
    source = EvidenceSource(
        index=1,
        source="data/references/guide.pdf",
        citation="WHO, 2008. A Guide (WHO/X/1)",
        section="Chapter 4 - Clinical",
        page=52,
        page_label="41",
    )
    assert source.reference() == "WHO, 2008. A Guide (WHO/X/1) — Chapter 4 - Clinical — p.41"


def test_reference_falls_back_to_the_file_name():
    source = EvidenceSource(index=1, source="data/references/guide.pdf")
    assert source.reference() == "guide.pdf"
