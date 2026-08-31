"""Build the Chroma vector store from the curated reference library.

Usage:
    python -m rag.ingest              # rebuild the store from scratch (default)
    python -m rag.ingest --append     # add to the existing store instead
    python -m rag.ingest --dry-run    # show what would be ingested, touch nothing

Rebuilding is the default because Chroma appends by default: re-running an ingest over
an existing store would embed the same files a second time, leaving duplicate chunks
that crowd out other content in retrieval results.

Only pages declared in data/references/sources.json are ingested. Everything else is
dropped before it is ever embedded — see rag/respiratory_corpus.py for the reasoning.
"""

import argparse
from collections import Counter
from pathlib import Path

from langchain_chroma import Chroma
from langchain_core.documents import Document

from config import CHROMA_PERSIST_DIR, REFERENCE_DIR
# Imported as a module, not as names. `ADDED_DIR` is a path the tests relocate, and a
# `from ... import ADDED_DIR` would bind whatever it happened to be when this module was
# first loaded — which is the real directory as soon as anything else imports rag.ingest
# first. Reading it through the module means the value is looked up when it is used.
from rag import added_documents
from rag.added_documents import ORIGIN_ADDED, ORIGIN_BUILTIN
from rag.chunking import chunk_documents, load_documents
from rag.embeddings import get_embeddings
from rag.respiratory_corpus import (
    SourceSpec,
    conditions_to_metadata,
    load_manifest,
    tag_conditions,
)


class CurationReport:
    def __init__(self):
        self.kept: Counter = Counter()
        self.dropped: Counter = Counter()
        self.undeclared: set[str] = set()

    def total_kept(self) -> int:
        return sum(self.kept.values())


def curate(
    documents: list[Document], manifest: dict[str, SourceSpec]
) -> tuple[list[Document], CurationReport]:
    """Keep only declared pages, and stamp each with its provenance."""
    report = CurationReport()
    kept: list[Document] = []

    for doc in documents:
        name = Path(doc.metadata.get("source", "")).name
        spec = manifest.get(name)
        if spec is None:
            report.undeclared.add(name)
            continue

        # PyPDFLoader numbers pages from 0; the manifest counts them the way a person
        # reading the PDF does.
        pdf_page = int(doc.metadata.get("page", 0)) + 1
        section = spec.section_for(pdf_page)
        if section is None:
            report.dropped[name] += 1
            continue

        doc.metadata.update(
            {
                "source_name": name,
                "title": spec.title,
                "citation": spec.citation,
                "section": section.name,
                "pdf_page": pdf_page,
                "page_label": str(doc.metadata.get("page_label", pdf_page)),
                # What makes this document unremovable from the application. Chunks
                # embedded before this key existed are read as built-in too, so the
                # protection did not need a re-embed to take effect.
                "origin": ORIGIN_BUILTIN,
            }
        )
        kept.append(doc)
        report.kept[name] += 1

    return kept, report


def tag_chunks(chunks: list[Document]) -> Counter:
    """Label each chunk with the conditions it mentions. Returns a histogram."""
    histogram: Counter = Counter()
    for chunk in chunks:
        conditions = tag_conditions(chunk.page_content)
        chunk.metadata["conditions"] = conditions_to_metadata(conditions)
        histogram.update(conditions or ["(untagged)"])
    return histogram


def collect_added(documents: list[Document]) -> list[Document]:
    """Pages belonging to physician-added documents, stamped from the added registry.

    Separated out before curation because these are ingested whole and have no manifest
    entry — `curate` would drop every one of them as undeclared. A rebuild that quietly
    emptied the added shelf would be a trap for whoever ran it.
    """
    known = added_documents.registry()
    kept: list[Document] = []

    for doc in documents:
        path = Path(doc.metadata.get("source", ""))
        if added_documents.ADDED_DIR not in path.parents:
            continue

        record = known.get(path.name)
        if record is None:
            # On the shelf but not in the registry: no title, so no citation, so nothing
            # the assistant could quote. Left alone and reported rather than guessed at.
            continue

        pdf_page = int(doc.metadata.get("page", 0)) + 1
        doc.metadata.update(
            {
                "source_name": path.name,
                "title": record["title"],
                "citation": added_documents.citation_for(
                    record["title"], record.get("publisher"),
                    record.get("year"), record.get("reference"),
                ),
                "section": "Whole document",
                "pdf_page": pdf_page,
                "page_label": str(doc.metadata.get("page_label", pdf_page)),
                "origin": ORIGIN_ADDED,
            }
        )
        kept.append(doc)

    return kept


def ingest(append: bool = False, dry_run: bool = False):
    manifest = load_manifest()
    documents = load_documents(REFERENCE_DIR)
    if not documents:
        print(f"No .pdf/.txt/.md files found under {REFERENCE_DIR}")
        return

    # The added shelf is taken whole and must not reach the curator, which would drop it.
    added = collect_added(documents)
    added_paths = {id(d) for d in added}
    documents = [d for d in documents if id(d) not in added_paths]

    pages_per_file = Counter(Path(d.metadata.get("source", "?")).name for d in documents)
    curated, report = curate(documents, manifest)

    print(f"Reference library: {REFERENCE_DIR}")
    for name in sorted(pages_per_file):
        total = pages_per_file[name]
        if name in report.undeclared:
            print(f"  - {name}: SKIPPED — not declared in sources.json ({total} pages)")
            continue
        kept = report.kept[name]
        print(f"  - {name}: {kept} of {total} pages ingested ({report.dropped[name]} dropped)")

    if report.undeclared:
        print(
            "\nUndeclared files were ignored. Add an entry to data/references/sources.json\n"
            "with the page ranges that contain clinical guidance to include them."
        )

    if not curated and not added:
        print("\nNothing to ingest.")
        return

    if added:
        by_file = Counter(d.metadata["source_name"] for d in added)
        print("\nPhysician-added documents (ingested whole):")
        for name, pages in sorted(by_file.items()):
            print(f"  - {name}: {pages} page(s)")

    chunks = chunk_documents(curated + added)
    histogram = tag_chunks(chunks)

    print(f"\nSplit {report.total_kept() + len(added)} page(s) into {len(chunks)} chunk(s).")
    print("Chunks mentioning each condition:")
    for condition, count in histogram.most_common():
        print(f"  {condition:<30} {count}")

    if dry_run:
        print("\n--dry-run: the vector store was not modified.")
        return

    embeddings = get_embeddings()
    if not append and Path(CHROMA_PERSIST_DIR).exists():
        # Drop the collection through Chroma rather than deleting the directory: on
        # Windows the store's files stay locked while any other process (e.g. a running
        # chat session) holds the DB open, and rmtree would fail.
        Chroma(
            persist_directory=CHROMA_PERSIST_DIR,
            embedding_function=embeddings,
        ).delete_collection()
        print("\nCleared the existing vector store.")

    vectorstore = Chroma.from_documents(
        documents=chunks,
        embedding=embeddings,
        persist_directory=CHROMA_PERSIST_DIR,
    )
    total = vectorstore._collection.count()
    print(f"Persisted vector store to {CHROMA_PERSIST_DIR} ({total} chunks total).")


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Ingest curated references into the vector store.")
    parser.add_argument(
        "--append",
        action="store_true",
        help="Add to the existing store instead of rebuilding it from scratch.",
    )
    parser.add_argument(
        "--dry-run",
        action="store_true",
        help="Report what would be ingested without touching the vector store.",
    )
    args = parser.parse_args()
    ingest(append=args.append, dry_run=args.dry_run)
