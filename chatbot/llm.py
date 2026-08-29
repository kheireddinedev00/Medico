"""Talking to the model, and getting a validated object back.

This module is shared plumbing, not clinical logic: it knows how to reach OpenRouter and
how to insist on JSON, and nothing about differentials or triage. Both AI modules depend
on it, which keeps `triage/` from having to import the medical assistant's consultation
code to borrow one function.
"""

from __future__ import annotations

from typing import Optional, Type, TypeVar

from langchain_core.messages import HumanMessage, SystemMessage
from langchain_openai import ChatOpenAI
from pydantic import BaseModel

from clinical.json_reply import ReplyError, parse_model
from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, OPENROUTER_MODEL

# Free-tier models on OpenRouter are heavily contended and return 429 often. The OpenAI
# SDK retries these with exponential backoff; the default of 2 attempts is not enough for
# a ":free" model, so we raise it. Harmless on paid models, which rarely hit 429.
DEFAULT_MAX_RETRIES = 6

T = TypeVar("T", bound=BaseModel)

# One corrective retry. Small free models occasionally wrap JSON in prose or drop a key;
# a single reminder recovers most of those. Beyond that the failure is real and the raw
# reply is surfaced rather than being retried into a different kind of wrong.
RETRY_NOTE = (
    "Your previous reply was not a single valid JSON object matching the required "
    "schema. Return ONLY the JSON object, with every key present, and no other text."
)


def get_llm(
    temperature: float = 0.2,
    max_retries: int = DEFAULT_MAX_RETRIES,
    model: Optional[str] = None,
    timeout: int = 120,
):
    """A chat model over OpenRouter.

    `model` and `timeout` are parameters rather than constants because the triage agent
    needs a short leash - it has a usable answer without the model and cannot afford to
    wait two minutes for a better one.
    """
    if not OPENROUTER_API_KEY:
        raise RuntimeError(
            "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key."
        )
    return ChatOpenAI(
        model=model or OPENROUTER_MODEL,
        api_key=OPENROUTER_API_KEY,
        base_url=OPENROUTER_BASE_URL,
        temperature=temperature,
        max_retries=max_retries,
        timeout=timeout,
    )


def run_structured(
    system_prompt: str,
    blocks: list[str],
    model_cls: Type[T],
    llm=None,
    error_cls: type = ReplyError,
) -> T:
    """One request to the model, parsed into `model_cls`, with a single corrective retry.

    Every service that asks the model for structured output goes through here, so they
    all share the same failure behaviour: try once, remind once, then surface the raw
    reply rather than retrying into a different kind of wrong.
    """
    llm = llm if llm is not None else get_llm()
    messages = [
        SystemMessage(content=system_prompt),
        HumanMessage(content="\n\n".join(blocks)),
    ]
    try:
        return parse_model(llm.invoke(messages).content, model_cls, error_cls)
    except error_cls:
        messages.append(HumanMessage(content=RETRY_NOTE))
        return parse_model(llm.invoke(messages).content, model_cls, error_cls)
