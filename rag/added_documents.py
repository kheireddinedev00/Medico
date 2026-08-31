"""Reference documents a physician added, and can take back out.

The curated library in `sources.json` is the corpus this project was built around: five
guidelines, page ranges chosen by hand, committed to the repository. It is not meant to be
edited from a web form, and nothing here can edit it.

What this module adds is a second, smaller shelf beside it. A physician can put a document
on that shelf and take it off again; the curated shelf is not reachable from either
operation. That asymmetry is the whole design, and it is enforced twice — once by where the
files live (`data/references/added/`, its own directory) and once by the `origin` stamped on
every chunk. A chunk with no `origin` is treated as built-in, so the 1,174 chunks that
predate this module are protected without needing to be re-embedded.

**Added documents are ingested whole.** The curated sources declare page ranges because a
138-page implementation manual holds about 24 pages of clinical guidance, and retrieving a
paragraph about district coordinators when the question was wheeze is worse than retrieving
nothing. That reasoning does not apply to a two-page protocol someone uploads on purpose:
asking them to nominate page ranges for a document they chose deliberately is friction
without a benefit. Size is the safeguard instead — see `MAX_BYTES`.

**The registry is here, not in the database.** `python -m rag.ingest` has to be able to
rebuild the store from the filesystem alone, and a rebuild that silently dropped every
added document would be a trap laid for whoever runs it next. So the shelf carries its own
manifest. The web application keeps its own row per document for audit and attribution;
that is a record of who did what, not the corpus itself.
"""

from __future__ import annotations

import json
import re
import unicodedata
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional

from langchain_core.documents import Document

from config import REFERENCE_DIR
from rag.chunking import LOADERS, chunk_documents
from rag.respiratory_corpus import conditions_to_metadata, tag_conditions

# Its own directory, so "added" and "curated" are visible on disk and can be gitignored
# separately. An uploaded guideline is no more redistributable than the ones already here.
ADDED_DIR = REFERENCE_DIR / "added"
REGISTRY = ADDED_DIR / "registry.json"

ORIGIN_BUILTIN = "builtin"
ORIGIN_ADDED = "added"

# What the chunker can actually read. Anything else would be stored and never retrieved,
# which looks like it worked and is worse than a refusal.
ALLOWED_SUFFIXES = tuple(LOADERS)

# Added documents are ingested whole, so size is what stands in for curation. Ten megabytes
# is a generous protocol or leaflet and a long way short of a full guideline — a physician
# trying to add one of those is really asking for the curated path, and should be told so
# rather than quietly having 600 chunks of it embedded.
MAX_BYTES = 10 * 1024 * 1024


class DocumentError(Exception):
    """A document that cannot be accepted, described for the person who sent it."""


class ProtectedDocument(DocumentError):
    """An attempt to remove part of the curated library."""


# ---------------------------------------------------------------------------- registry


def _read_registry() -> dict[str, dict]:
    if not REGISTRY.exists():
        return {}
    try:
        raw = json.loads(REGISTRY.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        raise DocumentError(f"The added-documents registry is not valid JSON: {exc}") from exc
    return {entry["source_name"]: entry for entry in raw.get("documents", [])}


def _write_registry(entries: dict[str, dict]) -> None:
    ADDED_DIR.mkdir(parents=True, exist_ok=True)
    REGISTRY.write_text(
        json.dumps(
            {
                "_note": (
                    "Documents added by a physician through the application. Unlike "
                    "sources.json these are ingested whole. Maintained by "
                    "rag/added_documents.py — edit the application, not this file."
                ),
                "documents": sorted(entries.values(), key=lambda e: e["added_at"]),
            },
            indent=2,
        ),
        encoding="utf-8",
    )


def registry() -> dict[str, dict]:
    """Every added document the corpus knows about, keyed by stored file name."""
    return _read_registry()


# ------------------------------------------------------------------------------ naming


def safe_source_name(filename: str) -> str:
    """Turn an uploaded file name into one that is safe to write and to key on.

    Uploaded names are attacker-controlled in the general case and merely untidy in this
    one; both are handled the same way. The path is discarded entirely rather than
    sanitised, because a name that has to be *made* safe is a name to stop trusting.
    """
    stem = Path(filename).name
    suffix = Path(stem).suffix.lower()

    if suffix not in ALLOWED_SUFFIXES:
        raise DocumentError(
            f"{suffix or 'That file type'} cannot be read. Accepted: "
            + ", ".join(sorted(ALLOWED_SUFFIXES))
        )

    base = unicodedata.normalize("NFKD", Path(stem).stem).encode("ascii", "ignore").decode()
    base = re.sub(r"[^A-Za-z0-9._-]+", "-", base).strip("-._")

    if not base:
        raise DocumentError("That file name has no usable characters in it.")

    return f"{base[:120]}{suffix}"


def citation_for(title: str, publisher: Optional[str], year: Optional[int],
                 reference: Optional[str]) -> str:
    """The same citation format the curated sources use, so quotes read alike."""
    parts = [p for p in (publisher, str(year) if year else None) if p]
    prefix = f"{', '.join(parts)}. " if parts else ""
    suffix = f" ({reference})" if reference else ""
    return f"{prefix}{title}{suffix}"


# ----------------------------------------------------------------------------- reading


def _load_file(path: Path) -> list[Document]:
    loader_cls = LOADERS.get(path.suffix.lower())
    if loader_cls is None:
        raise DocumentError(f"{path.suffix} cannot be read.")
    return loader_cls(str(path)).load()


def prepare_chunks(path: Path, source_name: str, title: str, citation: str) -> list[Document]:
    """Read one added document and return its chunks, stamped and tagged.

    The metadata deliberately matches what `ingest.curate` produces for a curated source,
    with `origin` added. Retrieval, citation rendering and the assistant's prompt all read
    these keys, and a chunk that carries different ones would be retrievable but unquotable.
    """
    pages = _load_file(path)

    if not pages:
        raise DocumentError(
            "Nothing could be read out of that file. A scanned PDF with no text layer "
            "looks like this — it needs to be run through OCR first."
        )

    for page in pages:
        pdf_page = int(page.metadata.get("page", 0)) + 1
        page.metadata.update(
            {
                "source_name": source_name,
                "title": title,
                "citation": citation,
                # Curated sources name the chapter a passage came from. An added document
                # is taken whole, and saying so is more honest than inventing a section.
                "section": "Whole document",
                "pdf_page": pdf_page,
                "page_label": str(page.metadata.get("page_label", pdf_page)),
                "origin": ORIGIN_ADDED,
            }
        )

    chunks = chunk_documents(pages)

    if not chunks:
        raise DocumentError("That file holds no text once split into passages.")

    for chunk in chunks:
        chunk.metadata["conditions"] = conditions_to_metadata(tag_conditions(chunk.page_content))
        # chunk_documents copies page metadata, but be explicit: this is the flag that
        # decides whether the document can ever be removed again.
        chunk.metadata["origin"] = ORIGIN_ADDED

    return chunks


# ------------------------------------------------------------------------- the corpus


def _collection():
    # Imported here rather than at module scope: loading the embedding model is slow, and
    # `safe_source_name` and the registry helpers are useful without it.
    from rag.retriever import get_vectorstore

    return get_vectorstore()


def list_documents() -> list[dict]:
    """Every document in the store, curated and added alike.

    Built by reading the chunks rather than the manifests, because the store is what the
    assistant actually retrieves from. A manifest entry whose chunks were never embedded
    would otherwise be listed as present.
    """
    store = _collection()
    got = store._collection.get(include=["metadatas"])

    documents: dict[str, dict] = {}

    for meta in got["metadatas"]:
        name = meta.get("source_name")
        if not name:
            continue

        entry = documents.get(name)
        if entry is None:
            documents[name] = entry = {
                "source_name": name,
                "title": meta.get("title") or name,
                "citation": meta.get("citation") or name,
                # Absent means built-in. Every chunk embedded before this module existed
                # has no origin, and defaulting those to removable would expose the
                # curated library to exactly the deletion this module refuses.
                "origin": meta.get("origin") or ORIGIN_BUILTIN,
                "chunks": 0,
            }
        entry["chunks"] += 1

    known = _read_registry()
    for name, entry in documents.items():
        record = known.get(name)
        if record:
            entry["added_at"] = record.get("added_at")
            entry["original_filename"] = record.get("original_filename")

    return sorted(
        documents.values(),
        key=lambda d: (d["origin"] != ORIGIN_ADDED, d["title"].lower()),
    )


def add_document(
    *,
    filename: str,
    content: bytes,
    title: str,
    publisher: Optional[str] = None,
    year: Optional[int] = None,
    reference: Optional[str] = None,
) -> dict:
    """Put one document on the added shelf and embed it.

    Writes the file only after the content has been read and chunked successfully, so a
    file that cannot be parsed does not leave a corpse in the reference directory for the
    next rebuild to pick up.
    """
    if not title or not title.strip():
        raise DocumentError("A document needs a title — it is what the citation is built from.")

    if len(content) > MAX_BYTES:
        raise DocumentError(
            f"That file is {len(content) / 1_048_576:.1f} MB. The limit is "
            f"{MAX_BYTES // 1_048_576} MB, because added documents are ingested whole. "
            "A full guideline belongs in the curated library, with its page ranges declared."
        )

    if not content:
        raise DocumentError("That file is empty.")

    source_name = safe_source_name(filename)
    title = title.strip()

    if source_name in _read_registry():
        raise DocumentError(
            f"A document called {source_name} has already been added. Remove it first, or "
            "rename the file."
        )

    store = _collection()
    clash = store._collection.get(where={"source_name": source_name}, include=[])
    if clash["ids"]:
        raise ProtectedDocument(
            f"{source_name} is already part of the curated library and cannot be replaced "
            "from here."
        )

    ADDED_DIR.mkdir(parents=True, exist_ok=True)
    path = ADDED_DIR / source_name
    path.write_bytes(content)

    citation = citation_for(title, publisher, year, reference)

    try:
        chunks = prepare_chunks(path, source_name, title, citation)
        store.add_documents(chunks)
    except Exception:
        # Do not leave a file behind that nothing in the store refers to. The next full
        # rebuild would ingest it, and nobody would know where it came from.
        path.unlink(missing_ok=True)
        raise

    entries = _read_registry()
    entries[source_name] = {
        "source_name": source_name,
        "original_filename": Path(filename).name,
        "title": title,
        "publisher": publisher,
        "year": year,
        "reference": reference,
        "added_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
    }
    _write_registry(entries)

    return {
        "source_name": source_name,
        "title": title,
        "citation": citation,
        "chunks": len(chunks),
        "origin": ORIGIN_ADDED,
    }


def remove_document(source_name: str) -> dict:
    """Take an added document back off the shelf, chunks and file together.

    Refuses anything it did not put there. The check is against the chunks in the store
    rather than the registry alone, because the store is what the assistant reads: a
    registry that disagreed with it must not be able to authorise a deletion.
    """
    store = _collection()
    found = store._collection.get(where={"source_name": source_name}, include=["metadatas"])

    if not found["ids"]:
        raise DocumentError(f"No document called {source_name} is in the reference library.")

    origins = {meta.get("origin") or ORIGIN_BUILTIN for meta in found["metadatas"]}

    if origins != {ORIGIN_ADDED}:
        raise ProtectedDocument(
            f"{source_name} is part of the curated reference library. Those documents are "
            "fixed — the assistant's evidence base is not editable from the application."
        )

    store._collection.delete(where={"source_name": source_name})

    (ADDED_DIR / source_name).unlink(missing_ok=True)

    entries = _read_registry()
    entries.pop(source_name, None)
    _write_registry(entries)

    return {"source_name": source_name, "removed_chunks": len(found["ids"])}
