"""Vision-language model client.

`VLMClient` is a tiny abstract interface: given a system prompt and a list of images,
return the model's raw text response. `OpenRouterVLMClient` implements it against
OpenRouter using the OpenAI-compatible SDK. To swap models later (a different Qwen size,
a self-hosted endpoint, or MedGemma-VL), either change the VLM_MODEL config string or
add a new subclass — nothing else in the pipeline changes.
"""

from __future__ import annotations

import time
from abc import ABC, abstractmethod

from openai import APIStatusError, OpenAI, RateLimitError
from PIL import Image

from config import OPENROUTER_API_KEY, OPENROUTER_BASE_URL, VLM_MAX_TOKENS, VLM_MODEL
from report_reader.preprocess import image_to_data_url

# Free-tier VL models on OpenRouter are frequently rate-limited upstream (429). These
# are transient, so we retry with exponential backoff before giving up.
_MAX_RETRIES = 5
_BASE_BACKOFF_SECONDS = 3


class VLMClient(ABC):
    @abstractmethod
    def extract(self, system_prompt: str, images: list[Image.Image]) -> str:
        """Return the model's raw text output for the given prompt + images."""
        raise NotImplementedError


class OpenRouterVLMClient(VLMClient):
    def __init__(
        self,
        model: str | None = None,
        temperature: float = 0.0,
        max_tokens: int | None = None,
    ):
        if not OPENROUTER_API_KEY:
            raise RuntimeError(
                "OPENROUTER_API_KEY is not set. Copy .env.example to .env and add your key."
            )
        self.model = model or VLM_MODEL
        # temperature 0 for deterministic, faithful transcription (accuracy > creativity).
        self.temperature = temperature
        self.max_tokens = max_tokens or VLM_MAX_TOKENS
        self.client = OpenAI(
            api_key=OPENROUTER_API_KEY,
            base_url=OPENROUTER_BASE_URL,
        )

    def extract(self, system_prompt: str, images: list[Image.Image]) -> str:
        content = [
            {"type": "image_url", "image_url": {"url": image_to_data_url(img)}}
            for img in images
        ]
        content.append(
            {
                "type": "text",
                "text": (
                    "Extract the structured JSON from these report page(s). "
                    "Return only the JSON object."
                ),
            }
        )

        messages = [
            {"role": "system", "content": system_prompt},
            {"role": "user", "content": content},
        ]

        last_error: Exception | None = None
        for attempt in range(_MAX_RETRIES):
            try:
                response = self.client.chat.completions.create(
                    model=self.model,
                    temperature=self.temperature,
                    max_tokens=self.max_tokens,
                    messages=messages,
                )
                return response.choices[0].message.content or ""
            except (RateLimitError, APIStatusError) as exc:
                # Only retry transient rate limits (429); re-raise anything else (auth,
                # 402 credits, bad request) immediately since retrying won't help.
                status = getattr(exc, "status_code", None)
                if status != 429:
                    raise
                last_error = exc
                if attempt < _MAX_RETRIES - 1:
                    wait = _BASE_BACKOFF_SECONDS * (2**attempt)
                    print(
                        f"Rate-limited (429), retrying in {wait}s "
                        f"[{attempt + 1}/{_MAX_RETRIES - 1}]..."
                    )
                    time.sleep(wait)

        raise RuntimeError(
            f"Model '{self.model}' stayed rate-limited after {_MAX_RETRIES} attempts. "
            "Try again later, switch VLM_MODEL (e.g. qwen/qwen2.5-vl-7b-instruct), or add "
            "your own provider key on OpenRouter."
        ) from last_error
