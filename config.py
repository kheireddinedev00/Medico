import os
from pathlib import Path

from dotenv import load_dotenv

load_dotenv()

BASE_DIR = Path(__file__).resolve().parent
DATA_DIR = BASE_DIR / "data"
# The curated reference library. Only what is declared in sources.json is ingested —
# see rag/respiratory_corpus.py for why nothing enters the corpus unlabelled.
REFERENCE_DIR = DATA_DIR / "references"
SOURCES_MANIFEST = REFERENCE_DIR / "sources.json"
# Optional curated ICD-10 subset. While its "codes" array is empty the assistant falls
# back to shape-checking suggested codes; fill it in and they get validated instead.
ICD10_CODE_LIST = DATA_DIR / "icd10_respiratory.json"

OPENROUTER_API_KEY = os.getenv("OPENROUTER_API_KEY", "")
OPENROUTER_BASE_URL = "https://openrouter.ai/api/v1"
OPENROUTER_MODEL = os.getenv("OPENROUTER_MODEL", "meta-llama/llama-3.1-8b-instruct")

CHROMA_PERSIST_DIR = str(BASE_DIR / os.getenv("CHROMA_PERSIST_DIR", "data/chroma_db"))
EMBEDDING_MODEL = os.getenv("EMBEDDING_MODEL", "sentence-transformers/all-MiniLM-L6-v2")

# --- Medical record store ---
# The local SQLite file standing in for the production database. It is the source of
# truth for patient profiles and visits; the assistant holds no patient state itself.
DB_PATH = str(BASE_DIR / os.getenv("DB_PATH", "data/clinic.db"))
# How many past encounters are summarised into the prompt. Enough for continuity of
# care without letting an old chart crowd out the current consultation.
CONTEXT_RECENT_VISITS = int(os.getenv("CONTEXT_RECENT_VISITS", "3"))
# How many uploaded reports are summarised into the prompt, newest first.
CONTEXT_RECENT_REPORTS = int(os.getenv("CONTEXT_RECENT_REPORTS", "4"))
# How many reference excerpts are retrieved per assessment. A differential spans several
# conditions, so it needs more evidence than a single-topic answer did.
EVIDENCE_CHUNKS = int(os.getenv("EVIDENCE_CHUNKS", "6"))

# --- Triage agent ---
# Every clinical threshold the triage agent uses. Unlike the ICD-10 list above there is
# no degraded mode: a missing or malformed rule set stops the agent rather than letting
# it sort patients on defaults nobody reviewed.
TRIAGE_RULES = DATA_DIR / os.getenv("TRIAGE_RULES", "triage_rules.json")
# Synthetic evaluation cases. No real patient data, ever.
TRIAGE_CASES_DIR = DATA_DIR / os.getenv("TRIAGE_CASES_DIR", "triage_cases")
# The interpretation model. Defaults to the same one the assistant uses, because the
# triage call is small and the project already has a working free-tier key for it.
TRIAGE_MODEL = os.getenv("TRIAGE_MODEL", OPENROUTER_MODEL)
# Hard ceiling on the interpretation call. A waiting room cannot wait two minutes for a
# free-tier model, and the rules layer has already produced a usable answer by then —
# so this timeout is short on purpose and expiring it is a normal outcome, not an error.
TRIAGE_LLM_TIMEOUT = int(os.getenv("TRIAGE_LLM_TIMEOUT", "20"))
# Retries for the interpretation call. Lower than the assistant's 6: triage degrades
# gracefully without the model, so waiting out a free-tier 429 storm costs more than
# it buys.
TRIAGE_LLM_RETRIES = int(os.getenv("TRIAGE_LLM_RETRIES", "1"))
# Set to "1" to skip the model entirely and run rules-only. Useful for the evaluation
# harness, for offline demos, and for measuring what the AI layer actually contributes.
TRIAGE_RULES_ONLY = os.getenv("TRIAGE_RULES_ONLY", "0") == "1"

# --- Report reader (Vision-Language model) ---
# Swap this string to change the VL model (e.g. "qwen/qwen2.5-vl-72b-instruct").
# Gemma is multimodal, so the same model — and the same Google BYOK key — serves both the
# chatbot and the report reader.
VLM_MODEL = os.getenv("VLM_MODEL", "google/gemma-4-31b-it:free")
# Cap on the model's response length. A report's JSON fits in a few thousand tokens;
# leaving this unset makes the API reserve the model's full ceiling and can 402 on
# limited credits. Raise it if you extract very large multi-panel reports.
VLM_MAX_TOKENS = int(os.getenv("VLM_MAX_TOKENS", "8192"))
# DPI used when rendering PDF pages to images. Higher = sharper small text, larger payload.
PDF_RENDER_DPI = int(os.getenv("PDF_RENDER_DPI", "200"))
# Images smaller than this on their long edge get upscaled before sending to the model.
MIN_IMAGE_LONG_EDGE = int(os.getenv("MIN_IMAGE_LONG_EDGE", "1600"))
