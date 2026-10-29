from dataclasses import dataclass
from typing import Protocol

import groq
from google import genai
from google.genai import errors, types
from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings

_retry = retry(stop=stop_after_attempt(2), wait=wait_exponential(min=1, max=4), reraise=True)


class LLMUnavailable(Exception):
    """All configured models failed (overloaded, retired, quota)."""


@dataclass
class Generation:
    text: str
    model: str
    prompt_tokens: int = 0
    completion_tokens: int = 0


class Generator(Protocol):
    def generate(self, system: str, prompt: str) -> Generation: ...


class GeminiClient:
    """Gemini handles embeddings (always) and generation (when LLM_PROVIDER=gemini)."""

    def __init__(self) -> None:
        s = get_settings()
        self._s = s
        self._client = genai.Client(api_key=s.gemini_api_key)

    @_retry
    def embed(self, texts: list[str], is_query: bool = False) -> list[list[float]]:
        task = "RETRIEVAL_QUERY" if is_query else "RETRIEVAL_DOCUMENT"
        out: list[list[float]] = []
        for i in range(0, len(texts), 100):  # API batch limit
            res = self._client.models.embed_content(
                model=self._s.embedding_model,
                contents=texts[i : i + 100],
                config=types.EmbedContentConfig(
                    task_type=task, output_dimensionality=self._s.embedding_dim
                ),
            )
            out.extend(_normalize(e.values) for e in res.embeddings)
        return out

    def generate(self, system: str, prompt: str) -> Generation:
        """Try the primary model, then the fallback if it is overloaded or unavailable."""
        models = [m for m in (self._s.gemini_model, self._s.gemini_fallback_model) if m]
        last_error: Exception | None = None
        for model in models:
            try:
                return self._generate_with(model, system, prompt)
            except (errors.ServerError, errors.ClientError) as e:
                last_error = e
        raise LLMUnavailable(str(last_error)) from last_error

    @_retry
    def _generate_with(self, model: str, system: str, prompt: str) -> Generation:
        res = self._client.models.generate_content(
            model=model,
            contents=prompt,
            config=types.GenerateContentConfig(system_instruction=system, temperature=0.1),
        )
        usage = res.usage_metadata
        return Generation(
            (res.text or "").strip(),
            model,
            getattr(usage, "prompt_token_count", 0) or 0,
            getattr(usage, "candidates_token_count", 0) or 0,
        )


class GroqClient:
    def __init__(self) -> None:
        s = get_settings()
        self._model = s.groq_model
        self._client = Groq(api_key=s.groq_api_key)

    def generate(self, system: str, prompt: str) -> Generation:
        try:
            return self._generate(system, prompt)
        except groq.APIError as e:
            raise LLMUnavailable(str(e)) from e

    @_retry
    def _generate(self, system: str, prompt: str) -> Generation:
        res = self._client.chat.completions.create(
            model=self._model,
            temperature=0.1,
            messages=[
                {"role": "system", "content": system},
                {"role": "user", "content": prompt},
            ],
        )
        usage = res.usage
        return Generation(
            (res.choices[0].message.content or "").strip(),
            self._model,
            getattr(usage, "prompt_tokens", 0) or 0,
            getattr(usage, "completion_tokens", 0) or 0,
        )


def _normalize(vec: list[float]) -> list[float]:
    # Truncated Gemini embeddings aren't unit length; normalize for cosine similarity.
    norm = sum(v * v for v in vec) ** 0.5 or 1.0
    return [v / norm for v in vec]


def get_embedder() -> GeminiClient:
    return GeminiClient()


def get_generator() -> Generator:
    return GroqClient() if get_settings().llm_provider == "groq" else GeminiClient()
