import re
from dataclasses import dataclass, field

# Pattern-based first line of defense. Cheap, deterministic, testable.
# Not a complete defense; the system prompt and output checks are the other layers.
INJECTION_PATTERNS = [
    r"ignore (all |any )?(previous|prior|above) (instructions|prompts?|rules)",
    r"disregard (all |any )?(previous|prior|above|your) (instructions|prompts?|rules)",
    r"(reveal|show|print|repeat) (me )?(your |the )?(system|hidden|initial) (prompt|instructions)",
    r"you are now (?!able)",
    r"\bjailbreak\b|\bDAN mode\b",
    r"pretend (that )?you (have|are) no (rules|restrictions|guidelines)",
]

TOXIC_TERMS = ["kill yourself", "i will hurt", "go die"]

PII_PATTERNS = {
    "EMAIL": r"[\w.+-]+@[\w-]+\.[\w.-]+",
    "SSN": r"\b\d{3}-\d{2}-\d{4}\b",
    "CARD": r"\b(?:\d[ -]?){13,16}\b",
    "PHONE": r"(?<!\d)(?:\+?\d{1,2}[ .-]?)?\(?\d{3}\)?[ .-]?\d{3}[ .-]?\d{4}(?!\d)",
}


@dataclass
class InputResult:
    text: str
    blocked: bool = False
    reason: str = ""
    flags: list[str] = field(default_factory=list)


def redact_pii(text: str) -> tuple[str, list[str]]:
    found: list[str] = []
    for label, pattern in PII_PATTERNS.items():  # order matters: SSN/CARD before PHONE
        new, n = re.subn(pattern, f"[{label}_REDACTED]", text)
        if n:
            found.append(f"pii_{label.lower()}")
            text = new
    return text, found


def check_input(question: str, max_chars: int) -> InputResult:
    question = question.strip()
    if not question:
        return InputResult(question, True, "Please enter a question.", ["empty"])
    if len(question) > max_chars:
        return InputResult(question, True, f"Question is too long (max {max_chars} characters).", ["too_long"])

    lowered = question.lower()
    if any(term in lowered for term in TOXIC_TERMS):
        return InputResult(question, True, "I can't help with abusive or harmful messages.", ["toxicity"])
    for pattern in INJECTION_PATTERNS:
        if re.search(pattern, lowered):
            return InputResult(
                question, True, "I can only answer questions about company policies.", ["prompt_injection"]
            )

    clean, flags = redact_pii(question)
    return InputResult(clean, False, "", flags)
