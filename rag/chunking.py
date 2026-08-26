from pathlib import Path

from langchain_community.document_loaders import PyPDFLoader, TextLoader
from langchain_core.documents import Document
from langchain_text_splitters import RecursiveCharacterTextSplitter

LOADERS = {
    ".pdf": PyPDFLoader,
    ".txt": TextLoader,
    ".md": TextLoader,
}


def load_documents(data_dir: Path) -> list[Document]:
    """Load every supported file under `data_dir`, recursively.

    Recursive so the reference library can be organised into folders per publisher as
    it grows. The previous top-level-only walk silently found nothing once the sources
    moved into data/references/.
    """
    documents = []
    for path in sorted(data_dir.rglob("*")):
        loader_cls = LOADERS.get(path.suffix.lower())
        if loader_cls is None or not path.is_file():
            continue
        documents.extend(loader_cls(str(path)).load())
    return documents


def chunk_documents(
    documents: list[Document],
    chunk_size: int = 800,
    chunk_overlap: int = 120,
) -> list[Document]:
    splitter = RecursiveCharacterTextSplitter(
        chunk_size=chunk_size,
        chunk_overlap=chunk_overlap,
        separators=["\n\n", "\n", ". ", " ", ""],
    )
    return splitter.split_documents(documents)
