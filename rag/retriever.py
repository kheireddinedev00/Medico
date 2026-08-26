"""Searching the reference library.

Retrieval uses MMR (maximal marginal relevance) rather than plain nearest-neighbour
similarity. The difference matters for a differential: plain top-k returns the k most
similar chunks, and when several of them are near-duplicates from the same passage the
assistant is handed the same fact six times. MMR trades a little similarity for
coverage, so the excerpts that come back span more of the material — which is what
reasoning across several candidate conditions needs.

`fetch_k` is the candidate pool MMR selects from; it must be comfortably larger than k
or there is nothing to diversify between.
"""

from langchain_chroma import Chroma

from config import CHROMA_PERSIST_DIR
from rag.embeddings import get_embeddings

# How much wider than k the candidate pool is. 4x keeps selection meaningful without
# scanning the whole collection on every request.
FETCH_MULTIPLIER = 4


def get_vectorstore() -> Chroma:
    return Chroma(
        persist_directory=CHROMA_PERSIST_DIR,
        embedding_function=get_embeddings(),
    )


def get_retriever(k: int = 4, diverse: bool = True):
    """Return a retriever over the reference library.

    Set `diverse=False` for plain similarity search — useful when comparing retrieval
    strategies, not for normal use.
    """
    vectorstore = get_vectorstore()
    if not diverse:
        return vectorstore.as_retriever(search_kwargs={"k": k})
    return vectorstore.as_retriever(
        search_type="mmr",
        search_kwargs={"k": k, "fetch_k": k * FETCH_MULTIPLIER},
    )
