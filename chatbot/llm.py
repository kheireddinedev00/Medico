from langchain_openai import ChatOpenAI

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL

# Free-tier models on OpenRouter are heavily contended and return 429 often. The OpenAI
# SDK retries these with exponential backoff; the default of 2 attempts is not enough for
# a ":free" model, so we raise it. Harmless on paid models, which rarely hit 429.
DEFAULT_MAX_RETRIES = 6


def get_llm(temperature: float = 0.2, max_retries: int = DEFAULT_MAX_RETRIES):
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return ChatOpenAI(
        model=OPENROUTER_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        temperature=temperature,
        max_retries=max_retries,
        timeout=120,
    )
