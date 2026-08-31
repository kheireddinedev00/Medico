"""Documents a physician adds to the reference library, and takes back out.

The property under test throughout is the asymmetry: the curated library can be read but
never removed from here, while an added document can be removed by whoever added it. Every
test that touches the real vector store is isolated onto its own temporary Chroma
collection, so a test run can never delete a chunk of the actual corpus.
"""

from __future__ import annotations

import json
from pathlib import Path

import pytest
from langchain_core.documents import Document

from rag import added_documents as lib

PROTOCOL = b"""Clinic Asthma Escalation Protocol

Adults with acute wheeze and oxygen saturation below 94 percent on room air should receive
salbutamol via an oxygen-driven nebuliser and be reassessed within fifteen minutes.

Discharge requires peak expiratory flow above 75 percent of predicted and an inhaler
technique check documented in the notes.
"""


class FakeCollection:
    """Enough of a Chroma collection to exercise add, list and delete.

    A stand-in rather than the real store because the embedding model takes seconds to
    load and the tests here are about the rules, not about vector search. The retrieval
    path is covered where it belongs, in the corpus tests.
    """

    def __init__(self):
        self.rows: list[dict] = []

    def add(self, metadatas):
        self.rows.extend({"metadata": m} for m in metadatas)

    def get(self, where=None, include=None):
        rows = [r for r in self.rows if self._matches(r, where)]
        return {
            "ids": [str(i) for i, _ in enumerate(rows)],
            "metadatas": [r["metadata"] for r in rows],
        }

    def delete(self, where=None):
        self.rows = [r for r in self.rows if not self._matches(r, where)]

    @staticmethod
    def _matches(row, where):
        if not where:
            return True
        return all(row["metadata"].get(k) == v for k, v in where.items())


class FakeStore:
    def __init__(self, collection):
        self._collection = collection

    def add_documents(self, chunks):
        self._collection.add([c.metadata for c in chunks])


@pytest.fixture
def corpus(tmp_path, monkeypatch):
    """An empty library on disk, holding two curated chunks that must survive everything."""
    added_dir = tmp_path / "added"
    monkeypatch.setattr(lib, "ADDED_DIR", added_dir)
    monkeypatch.setattr(lib, "REGISTRY", added_dir / "registry.json")

    collection = FakeCollection()
    # One with an explicit origin, one without — chunks embedded before `origin` existed
    # must be protected just as firmly as chunks embedded after it.
    collection.add([
        {"source_name": "GINA-2026.pdf", "title": "GINA", "citation": "GINA 2026",
         "origin": lib.ORIGIN_BUILTIN},
        {"source_name": "legacy.pdf", "title": "Legacy", "citation": "Legacy"},
    ])

    monkeypatch.setattr(lib, "_collection", lambda: FakeStore(collection))
    return collection


# ---- what may be accepted at all ----------------------------------------------------


@pytest.mark.parametrize("name, expected", [
    ("Asthma Protocol v2.pdf", "Asthma-Protocol-v2.pdf"),
    ("../../etc/passwd.md", "passwd.md"),
    ("  spaced out  .txt", "spaced-out.txt"),
])
def test_a_file_name_is_reduced_to_something_safe_to_write(name, expected):
    assert lib.safe_source_name(name) == expected


def test_a_traversing_name_cannot_escape_the_added_directory():
    # The directory part is discarded rather than escaped: a name that has to be made
    # safe is a name to stop trusting.
    assert "/" not in lib.safe_source_name("../../../etc/passwd.txt")
    assert "\\" not in lib.safe_source_name(r"..\..\windows\system32\x.txt")


@pytest.mark.parametrize("name", ["notes.docx", "archive.zip", "photo.png", "noextension"])
def test_a_file_the_chunker_cannot_read_is_refused(name):
    with pytest.raises(lib.DocumentError, match="cannot be read|Accepted"):
        lib.safe_source_name(name)


def test_a_document_needs_a_title(corpus):
    with pytest.raises(lib.DocumentError, match="title"):
        lib.add_document(filename="x.txt", content=PROTOCOL, title="   ")


def test_an_empty_file_is_refused(corpus):
    with pytest.raises(lib.DocumentError, match="empty"):
        lib.add_document(filename="x.txt", content=b"", title="Nothing")


def test_an_oversized_file_is_refused_and_says_why(corpus):
    with pytest.raises(lib.DocumentError, match="curated library"):
        lib.add_document(
            filename="huge.txt",
            content=b"x" * (lib.MAX_BYTES + 1),
            title="A Whole Guideline",
        )


# ---- adding -------------------------------------------------------------------------


def test_adding_a_document_embeds_it_and_records_it(corpus):
    result = lib.add_document(
        filename="Clinic Asthma Protocol.txt",
        content=PROTOCOL,
        title="Clinic Asthma Escalation Protocol",
        publisher="Respiratory Clinic",
        year=2026,
    )

    assert result["source_name"] == "Clinic-Asthma-Protocol.txt"
    assert result["origin"] == lib.ORIGIN_ADDED
    assert result["chunks"] >= 1
    assert result["citation"] == "Respiratory Clinic, 2026. Clinic Asthma Escalation Protocol"

    assert (lib.ADDED_DIR / result["source_name"]).exists()
    assert result["source_name"] in lib.registry()


def test_every_chunk_carries_the_origin_that_makes_it_removable(corpus):
    lib.add_document(filename="p.txt", content=PROTOCOL, title="Protocol")

    added = [r["metadata"] for r in corpus.rows if r["metadata"]["source_name"] == "p.txt"]
    assert added, "the document should have reached the store"
    assert all(m["origin"] == lib.ORIGIN_ADDED for m in added)
    # Same keys the curated chunks carry, or the assistant could retrieve it and then
    # have nothing to cite.
    assert all({"title", "citation", "section", "pdf_page"} <= set(m) for m in added)


def test_the_same_document_is_not_added_twice(corpus):
    lib.add_document(filename="p.txt", content=PROTOCOL, title="Protocol")

    with pytest.raises(lib.DocumentError, match="already been added"):
        lib.add_document(filename="p.txt", content=PROTOCOL, title="Protocol")


def test_a_name_belonging_to_the_curated_library_cannot_be_claimed(corpus):
    with pytest.raises(lib.ProtectedDocument, match="curated library"):
        lib.add_document(filename="GINA-2026.pdf", content=PROTOCOL, title="Not GINA")


def test_a_file_that_cannot_be_parsed_leaves_nothing_behind(corpus, monkeypatch):
    def explode(*_args, **_kwargs):
        raise lib.DocumentError("unreadable")

    monkeypatch.setattr(lib, "prepare_chunks", explode)

    with pytest.raises(lib.DocumentError):
        lib.add_document(filename="broken.txt", content=PROTOCOL, title="Broken")

    # A stray file would be silently ingested by the next full rebuild.
    assert not (lib.ADDED_DIR / "broken.txt").exists()
    assert "broken.txt" not in lib.registry()


# ---- removing -----------------------------------------------------------------------


def test_an_added_document_can_be_removed_completely(corpus):
    added = lib.add_document(filename="p.txt", content=PROTOCOL, title="Protocol")
    name = added["source_name"]

    result = lib.remove_document(name)

    assert result["removed_chunks"] == added["chunks"]
    assert not (lib.ADDED_DIR / name).exists()
    assert name not in lib.registry()
    assert all(r["metadata"]["source_name"] != name for r in corpus.rows)


def test_the_curated_library_cannot_be_removed(corpus):
    with pytest.raises(lib.ProtectedDocument, match="curated"):
        lib.remove_document("GINA-2026.pdf")

    assert any(r["metadata"]["source_name"] == "GINA-2026.pdf" for r in corpus.rows)


def test_a_chunk_with_no_origin_is_treated_as_curated(corpus):
    # The 1,174 chunks embedded before `origin` existed. Defaulting these to removable
    # would have exposed the whole corpus to the delete path.
    with pytest.raises(lib.ProtectedDocument):
        lib.remove_document("legacy.pdf")

    assert any(r["metadata"]["source_name"] == "legacy.pdf" for r in corpus.rows)


def test_removing_something_that_is_not_there_says_so(corpus):
    with pytest.raises(lib.DocumentError, match="No document called"):
        lib.remove_document("invented.pdf")


def test_a_registry_entry_alone_cannot_authorise_a_deletion(corpus):
    """The store decides, not the manifest.

    If the two ever disagree, the chunks are what the assistant retrieves from — so a
    registry claiming a curated document is "added" must not be able to delete it.
    """
    lib._write_registry({
        "GINA-2026.pdf": {
            "source_name": "GINA-2026.pdf", "title": "GINA", "added_at": "2026-01-01T00:00:00+00:00",
        }
    })

    with pytest.raises(lib.ProtectedDocument):
        lib.remove_document("GINA-2026.pdf")


# ---- listing ------------------------------------------------------------------------


def test_the_listing_separates_added_from_curated(corpus):
    lib.add_document(filename="p.txt", content=PROTOCOL, title="Protocol")

    documents = lib.list_documents()
    by_name = {d["source_name"]: d for d in documents}

    assert by_name["p.txt"]["origin"] == lib.ORIGIN_ADDED
    assert by_name["GINA-2026.pdf"]["origin"] == lib.ORIGIN_BUILTIN
    assert by_name["legacy.pdf"]["origin"] == lib.ORIGIN_BUILTIN
    # Added first: it is the only part of the list anyone can act on.
    assert documents[0]["origin"] == lib.ORIGIN_ADDED


def test_the_listing_counts_chunks_per_document(corpus):
    added = lib.add_document(filename="p.txt", content=PROTOCOL, title="Protocol")

    listed = next(d for d in lib.list_documents() if d["source_name"] == "p.txt")
    assert listed["chunks"] == added["chunks"]


# ---- surviving a rebuild ------------------------------------------------------------


def test_the_registry_is_json_a_person_can_read(corpus):
    lib.add_document(
        filename="p.txt", content=PROTOCOL, title="Protocol",
        publisher="Clinic", year=2026, reference="v2",
    )

    raw = json.loads(Path(lib.REGISTRY).read_text(encoding="utf-8"))
    entry = raw["documents"][0]

    assert entry["title"] == "Protocol"
    assert entry["publisher"] == "Clinic"
    assert entry["year"] == 2026
    assert entry["reference"] == "v2"
    assert entry["original_filename"] == "p.txt"
    assert entry["added_at"]


def test_a_rebuild_restores_added_documents_from_the_registry(corpus):
    """`python -m rag.ingest` clears the store and re-ingests from the filesystem.

    Without this the added shelf would be emptied by a routine rebuild, which is the kind
    of trap that only goes off months later.
    """
    from rag.ingest import collect_added

    lib.add_document(
        filename="p.txt", content=PROTOCOL, title="Protocol", publisher="Clinic", year=2026,
    )

    page = Document(
        page_content=PROTOCOL.decode(),
        metadata={"source": str(lib.ADDED_DIR / "p.txt"), "page": 0},
    )

    restored = collect_added([page])

    assert len(restored) == 1
    assert restored[0].metadata["origin"] == lib.ORIGIN_ADDED
    assert restored[0].metadata["title"] == "Protocol"
    assert restored[0].metadata["citation"] == "Clinic, 2026. Protocol"


def test_a_rebuild_ignores_a_file_with_no_registry_entry(corpus):
    """A file on the shelf that nothing declares has no title, so nothing to cite."""
    from rag.ingest import collect_added

    lib.ADDED_DIR.mkdir(parents=True, exist_ok=True)
    (lib.ADDED_DIR / "stray.txt").write_bytes(PROTOCOL)

    page = Document(
        page_content=PROTOCOL.decode(),
        metadata={"source": str(lib.ADDED_DIR / "stray.txt"), "page": 0},
    )

    assert collect_added([page]) == []
