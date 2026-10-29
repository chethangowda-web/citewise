"""Scan document chunks for indirect prompt injection at upload time.

A poisoned document does not attack through the chat box: it waits to be retrieved and then tries to
instruct the model from inside the context ("when summarising, always say X"). Chunks that look like that
are quarantined: they stay in the database for review but are never retrieved or shown to the model.
"""
import logging
import re

from app.config import get_settings
from app.guardrails.input import INJECTION_PATTERNS
from app.guardrails.ml_guard import ml_screen

log = logging.getLogger("rag.document_scan")

# Patterns specific to instructions hidden inside documents (searched in lower-cased text).
DOCUMENT_PATTERNS = [
    r"(?m)^\s*(system|assistant|developer)\s*:",
    r"<\s*/?\s*(instructions?|system|prompt)\s*>",
    r"\b(when|if) (you are |you're )?(asked|summari[sz]ing|answering)\b.{0,80}\b(always|must|should) (say|respond|reply|answer|output)\b",
    r"\b(do not|don't|never) (tell|reveal|mention|cite) (this to )?(the )?(user|human|reader)\b",
    r"\bnew (instructions?|task)\s*:",
    r"\bignore (the )?(policy|context|passages|documents|above|rest)\b",
]


def regex_reasons(text: str) -> list[str]:
    """The rule-based part of the scan (no network)."""
    low = text.lower()
    reasons = []
    if any(re.search(p, low) for p in INJECTION_PATTERNS):
        reasons.append("injection_pattern")
    if any(re.search(p, low) for p in DOCUMENT_PATTERNS):
        reasons.append("document_instruction")
    return reasons


def scan_chunk(text: str) -> list[str]:
    """Reasons this chunk looks like an injection attempt (empty list = clean)."""
    reasons = regex_reasons(text)
    if not reasons and get_settings().ml_guard == "groq":
        blocked, _flags, score = ml_screen(text)  # fails open: an outage must not block uploads
        if blocked:
            reasons.append(f"ml_guard:{score:.2f}")
    return reasons
