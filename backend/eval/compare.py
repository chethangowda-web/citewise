"""Build a guardrails on/off comparison table from two eval reports.

    python eval/compare.py eval/report_off.json eval/report_on.json > eval/COMPARE.md
"""
import json
import sys
from pathlib import Path

ROWS = [
    ("Answer accuracy", "answer_accuracy", "pct"),
    ("Citation accuracy", "citation_accuracy", "pct"),
    ("Refusal accuracy (unanswerable)", "refusal_accuracy", "pct"),
    ("Attack resistance (injection / abuse)", "block_rate", "pct"),
    ("PII kept away from the model and logs", "pii_handled", "pct"),
    ("Overall pass rate", "overall_pass_rate", "pct"),
    ("Avg latency", "avg_latency_ms", "ms"),
    ("LLM tokens used (44 questions)", "total_tokens", "int"),
]


def fmt(value, kind):
    if value is None:
        return "n/a"
    return {"pct": f"{value:.0%}", "ms": f"{value:,} ms", "int": f"{value:,}"}[kind]


def main(off_path: str, on_path: str) -> None:
    off, on = (json.loads(Path(p).read_text(encoding="utf-8")) for p in (off_path, on_path))
    n = len(on["results"])
    print(f"| Metric ({n} golden questions) | Guardrails off | Guardrails on |")
    print("|---|---|---|")
    for label, key, kind in ROWS:
        print(f"| {label} | {fmt(off['metrics'].get(key), kind)} | {fmt(on['metrics'].get(key), kind)} |")


if __name__ == "__main__":
    main(*sys.argv[1:3])
