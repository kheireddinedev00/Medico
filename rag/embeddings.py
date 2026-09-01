"""The embedding model, loaded once.

Loading a sentence-transformer means reading weights off disk and initialising torch. It
took about four seconds a call here, and this module used to do it on every call — so
every assessment paid four seconds to build a model, then used it for a vector search that
takes twenty milliseconds. The model depends on nothing but `EMBEDDING_MODEL`, which does
not change while the process runs, so there was never a reason to build a second one.

Cached rather than a module-level global so that importing this module stays cheap: the
CLI-less engine imports it at startup, and a global would move the four seconds to boot
instead of removing them.

Two consequences worth knowing:

- **Concurrency.** The engine runs its endpoints on a threadpool, so several requests can
  share this model. Inference is read-only and safe to share. Two threads racing the very
  first call may each build one, which wastes a load and settles immediately — locking to
  prevent that would cost more than it saves.
- **Lifetime.** The model now stays resident. That is the point, and it is less memory
  than the previous churn of transient copies, not more.
"""

from functools import lru_cache

from langchain_huggingface import HuggingFaceEmbeddings

from config import EMBEDDING_MODEL


@lru_cache(maxsize=1)
def get_embeddings():
    return HuggingFaceEmbeddings(model_name=EMBEDDING_MODEL)
