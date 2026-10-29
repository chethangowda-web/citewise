"""Second-layer prompt-injection detector: Meta's Llama Prompt Guard 2 served by Groq.

The regex filter in input.py is fast and deterministic but only catches known phrasings. This classifier
returns a 0..1 injection/jailbreak score and generalises to paraphrases, other languages and some
obfuscation. It is a network call, so it runs only after the regex filter passes, and it can be set to
fail open (default: an outage must not take the chatbot down) or closed.
"""
import logging
from functools import lru_cache

import groq

from app.config import get_settings

log = logging.getLogger("rag.ml_guard")


class GuardUnavailable(Exception):
    """The classifier could not be reached or returned something unusable."""


class GroqPromptGuard:
    def __init__(self, api_key: str, model: str, timeout: float) -> None:
        self._client = groq.Groq(api_key=api_key, max_retries=0, timeout=timeout)
        self._model = model

    def score(self, text: str) -> float:
        try:
            res = self._client.chat.completions.create(
                model=self._model, messages=[{"role": "user", "content": text[:4000]}]
            )
            return float(res.choices[0].message.content.strip())
        except (groq.APIError, ValueError, TypeError, IndexError, AttributeError) as e:
            raise GuardUnavailable(str(e)) from e


@lru_cache
def get_guard() -> GroqPromptGuard:
    s = get_settings()
    return GroqPromptGuard(s.groq_api_key, s.ml_guard_model, s.ml_guard_timeout)


def ml_screen(text: str, guard=None) -> tuple[bool, list[str], float | None]:
    """Returns (blocked, flags, score). Disabled (the default) means a no-op."""
    s = get_settings()
    if s.ml_guard != "groq":
        return False, [], None
    guard = guard or get_guard()
    try:
        score = guard.score(text)
    except GuardUnavailable as e:
        log.warning("ml_guard_unavailable", extra={"error": str(e)[:300]})
        return (not s.ml_guard_fail_open), ["ml_guard_unavailable"], None
    if score >= s.ml_guard_threshold:
        return True, ["prompt_injection_ml"], score
    return False, [], score
