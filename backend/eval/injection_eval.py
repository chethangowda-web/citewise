"""Compare prompt-injection detectors: regex filter vs ML classifier vs both.

Run inside the backend container (needs GROQ_API_KEY; ~100 tiny classifier calls, no LLM tokens to speak of):

    docker compose run --rm --no-deps -v "$PWD/backend/eval:/app/eval" backend python -m eval.injection_eval

The ML threshold is chosen on the `dev` split only (maximising recall minus false-positive rate). Every
headline number is then reported on the held-out `test` split, so the threshold is not tuned on what is
reported. Scores are cached on disk, so re-running costs nothing.
"""
import hashlib
import json
import statistics
import time
from collections import defaultdict
from pathlib import Path

import groq

from app.config import get_settings
from app.guardrails.input import check_input
from app.guardrails.ml_guard import GroqPromptGuard
from app.rag.chunking import chunk_text

HERE = Path(__file__).parent
ITEMS = HERE / "adversarial.json"
CACHE = HERE / ".cache" / "guard_scores.json"
CORPUS = Path("/data/eval_corpus")
OUT_MD = HERE / "INJECTION.md"
OUT_JSON = HERE / "injection_report.json"
GRID = [round(x / 20, 2) for x in range(1, 20)]  # 0.05 .. 0.95


def regex_flags(text: str) -> bool:
    return "prompt_injection" in check_input(text, max_chars=10**6).flags


def load_scores(texts: list[str]) -> tuple[dict[str, float], list[float]]:
    s = get_settings()
    key = lambda t: hashlib.sha256(f"{s.ml_guard_model}|{t}".encode()).hexdigest()  # noqa: E731
    cache: dict[str, dict] = json.loads(CACHE.read_text()) if CACHE.exists() else {}
    guard = GroqPromptGuard(s.groq_api_key, s.ml_guard_model, timeout=15)
    fresh_latency = []
    for t in texts:
        if key(t) in cache:
            continue
        for attempt in range(6):
            start = time.perf_counter()
            try:
                score = guard.score(t)
                break
            except Exception as e:  # noqa: BLE001
                if attempt == 5:
                    raise
                print(f"  classifier error ({str(e)[:60]}); retrying in 10s", flush=True)
                time.sleep(10)
        ms = (time.perf_counter() - start) * 1000
        cache[key(t)] = {"score": score, "ms": ms}
        fresh_latency.append(ms)
        CACHE.parent.mkdir(exist_ok=True)
        CACHE.write_text(json.dumps(cache))
    lat = [cache[key(t)]["ms"] for t in texts]
    return {t: cache[key(t)]["score"] for t in texts}, (fresh_latency or lat)


def rates(items, flagged) -> tuple[int, int, int, int]:
    attacks = [i for i in items if i["label"] == "attack"]
    benign = [i for i in items if i["label"] == "benign"]
    tp = sum(flagged(i) for i in attacks)
    fp = sum(flagged(i) for i in benign)
    return tp, len(attacks), fp, len(benign)


def main() -> None:
    items = json.loads(ITEMS.read_text(encoding="utf-8"))
    chunks = [c for f in sorted(CORPUS.glob("*.md")) for c in chunk_text(f.read_text(encoding="utf-8"))]
    scores, latency = load_scores([i["text"] for i in items] + chunks)
    for i in items:
        i["regex"] = regex_flags(i["text"])
        i["ml"] = scores[i["text"]]

    dev = [i for i in items if i["split"] == "dev"]
    test = [i for i in items if i["split"] == "test"]

    def objective(t: float) -> float:
        tp, na, fp, nb = rates(dev, lambda i: i["regex"] or i["ml"] >= t)
        return tp / na - fp / nb

    best_t = max(GRID, key=lambda t: (round(objective(t), 6), t))  # ties: prefer the higher (stricter) threshold
    det = {
        "Regex filter only": lambda i: i["regex"],
        f"ML classifier only (t={best_t})": lambda i: i["ml"] >= best_t,
        f"Regex + ML (t={best_t}, tuned on dev)": lambda i: i["regex"] or i["ml"] >= best_t,
        "Regex + ML (t=0.5, library default)": lambda i: i["regex"] or i["ml"] >= 0.5,
    }
    chosen = f"Regex + ML (t={best_t}, tuned on dev)"
    pct = lambda a, b: f"{a}/{b} ({a / b:.0%})"  # noqa: E731

    lines = [
        f"Held-out **test** split: {sum(i['label'] == 'attack' for i in test)} attacks, {sum(i['label'] == 'benign' for i in test)} benign "
        f"(threshold chosen on the separate dev split: {best_t}).",
        "",
        "| Detector | Attacks caught (recall) | Benign wrongly flagged (false positives) |",
        "|---|---|---|",
    ]
    report = {"threshold": best_t, "test": {}, "all": {}}
    for name, fn in det.items():
        tp, na, fp, nb = rates(test, fn)
        report["test"][name] = {"tp": tp, "attacks": na, "fp": fp, "benign": nb}
        lines.append(f"| {name} | {pct(tp, na)} | {pct(fp, nb)} |")

    lines += ["", "Recall by attack technique (all 42 attacks, dev + test, threshold fixed from dev; a few items per technique, so read as descriptive):", "",
              "| Technique | n | Regex only | Regex + ML |", "|---|---|---|---|"]
    by_cat = defaultdict(list)
    for i in items:
        if i["label"] == "attack":
            by_cat[i["category"]].append(i)
    for cat, rows in by_cat.items():
        r = sum(det["Regex filter only"](i) for i in rows)
        c = sum(det[chosen](i) for i in rows)
        lines.append(f"| {cat} | {len(rows)} | {r}/{len(rows)} | {c}/{len(rows)} |")

    chunk_fp = sum(scores[c] >= best_t for c in chunks)
    chunk_regex = sum(regex_flags(c) for c in chunks)
    lines += ["", f"False quarantines on real policy content: the ML classifier at t={best_t} flags {chunk_fp}/{len(chunks)} of the corpus "
              f"chunks; the regex flags {chunk_regex}/{len(chunks)}."]

    misses = [i for i in test if i["label"] == "attack" and not det[chosen](i)]
    fps = [i for i in test if i["label"] == "benign" and det[chosen](i)]
    lines += ["", "Test attacks the combined detector still misses: " + (", ".join(f"`{i['text'][:70]}` ({i['category']}, ml={i['ml']:.2f})" for i in misses) or "none")]
    lines += ["", "Benign test items it wrongly blocks: " + (", ".join(f"`{i['text'][:70]}` (regex={i['regex']}, ml={i['ml']:.2f})" for i in fps) or "none")]
    p95 = sorted(latency)[int(len(latency) * 0.95) - 1] if len(latency) > 1 else latency[0]
    lines += ["", f"Classifier latency per call: mean {statistics.mean(latency):.0f} ms, p95 {p95:.0f} ms (it runs only when the regex filter passes)."]

    report["chunk_false_quarantine"] = {"ml": chunk_fp, "regex": chunk_regex, "chunks": len(chunks)}
    report["latency_ms"] = {"mean": statistics.mean(latency), "p95": p95}
    OUT_JSON.write_text(json.dumps(report, indent=1), encoding="utf-8")
    OUT_MD.write_text("\n".join(lines) + "\n", encoding="utf-8")
    print("\n".join(lines))
    print(f"\nwrote {OUT_MD.name} and {OUT_JSON.name}")


if __name__ == "__main__":
    main()
