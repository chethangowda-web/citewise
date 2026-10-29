import re
from dataclasses import dataclass, field

from app.guardrails.input import redact_pii

CITATION_RE = re.compile(r"\[(\d+)\]")
# Models sometimes emit 【1】, 【1†L3-L6】 or [1, 2]; normalize all of them to plain [n] markers.
_VARIANT_RE = re.compile(r"[\[【]\s*(\d+(?:\s*[,;]\s*\d+)*)(?:†[^\]】]*)?\s*[\]】]")
_REPEATED_RE = re.compile(r"(?P<c>\[\d+\])(?:\s*(?P=c))+")


def _normalize_citations(text: str) -> str:
    text = _VARIANT_RE.sub(lambda m: "".join(f"[{n}]" for n in re.findall(r"\d+", m.group(1))), text)
    text = _REPEATED_RE.sub(r"\g<c>", text)
    return re.sub(r"[  ]", " ", text)  # narrow/no-break spaces the model likes to emit


NO_ANSWER = "I couldn't find this in the company policy documents, so I can't answer it reliably."


@dataclass
class OutputResult:
    answer: str
    cited: list[int]
    flags: list[str] = field(default_factory=list)


def check_output(answer: str, num_sources: int) -> OutputResult:
    """Enforce grounding: the answer must cite at least one real source.

    - Citations pointing at non-existent sources are stripped (hallucinated refs).
    - An answer with no valid citation is replaced by the refusal message.
    - PII that leaked into the answer is redacted.
    """
    flags: list[str] = []
    answer = _normalize_citations(answer.strip())

    def _keep(m: re.Match) -> str:
        return m.group(0) if 1 <= int(m.group(1)) <= num_sources else ""

    cleaned = CITATION_RE.sub(_keep, answer)
    if cleaned != answer:
        flags.append("invalid_citation_removed")
    cited = sorted({int(n) for n in CITATION_RE.findall(cleaned)})

    if not cited:
        return OutputResult(NO_ANSWER, [], flags + ["ungrounded_answer"])

    cleaned, pii = redact_pii(cleaned)
    return OutputResult(cleaned, cited, flags + pii)
