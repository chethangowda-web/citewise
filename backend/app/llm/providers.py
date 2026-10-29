import logging
from dataclasses import dataclass
from typing import Protocol

import groq
from google import genai
from google.genai import errors, types
from groq import Groq
from tenacity import retry, stop_after_attempt, wait_exponential

from app.config import get_settings

log = logging.getLogger("rag.llm")

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


class FallbackGenerator:
    """Tries each generator in order; moves on when one is out of quota or down."""

    def __init__(self, generators: list[Generator]) -> None:
        self._generators = generators

    def generate(self, system: str, prompt: str) -> Generation:
        last: LLMUnavailable | None = None
        for i, gen in enumerate(self._generators):
            try:
                return gen.generate(system, prompt)
            except LLMUnavailable as e:
                last = e
                if i + 1 < len(self._generators):
                    log.warning("llm_fallback", extra={"failed": type(gen).__name__, "error": str(e)[:300]})
        raise LLMUnavailable(str(last)) from last


def get_generator() -> Generator:
    s = get_settings()
    groq_first = s.llm_provider == "groq"
    chain: list[Generator] = [GroqClient() if groq_first else GeminiClient()]
    other_key = s.gemini_api_key if groq_first else s.groq_api_key
    if s.llm_fallback and other_key:
        chain.append(GeminiClient() if groq_first else GroqClient())
    return chain[0] if len(chain) == 1 else FallbackGenerator(chain)
