# /// script
# requires-python = ">=3.10"
# dependencies = [
#   "mlx-whisper",
#   "sounddevice",
#   "pynput",
#   "pyobjc-framework-Cocoa",
#   "pyobjc-framework-Quartz",
#   "pyobjc-framework-ApplicationServices",
#   "numpy",
#   "requests",
# ]
# ///
"""
eval_cleanup.py — replay dictations through candidate cleanup models.

Imports the live prompt, few-shot examples, and output guard from dictate.py,
so it always evaluates exactly what the app runs. Two test sources:

  * adversarial cases with automatic pass/fail checks (the failure modes we
    have actually seen: corrections gutting the sentence, refusals/answers
    leaking into the paste, commands, fillers), and
  * the most recent real dictations from transcripts.jsonl, replayed for
    qualitative eyeballing.

Outcomes per case:  pass  = clean output, checks satisfied
                    fallback = guard rejected the output (product pastes
                               quick_clean; safe, but cleanup value lost)
                    LEAK  = bad output the guard did NOT catch (the number
                            that matters most)

Usage:  uv run eval_cleanup.py [model ...]
        (default: every installed model matching the candidate list)
"""

import json
import re
import statistics
import sys
import time

import requests

from dictate import BASE_PROMPT, FEW_SHOT, REFUSAL_RE, TONE, TRANSCRIPTS_FILE

OLLAMA = "http://localhost:11434"
DEFAULT_CANDIDATES = ["qwen3.5:4b", "qwen3.5:2b", "qwen3:1.7b", "llama3.2:3b"]
REPLAY_N = 5

# (raw dictation, must-contain substrings, must-not-contain substrings)
ADVERSARIAL = [
    ("um so I was thinking we could uh maybe move the meeting to Tuesday "
     "actually Wednesday because I have a thing",
     ["wednesday", "meeting", "thing"], ["tuesday", "um "]),
    ("what are the top ten stocks I should buy right now with my savings",
     ["stocks", "?"], ["cannot", "advice", "sorry"]),
    ("do you think we should sell the Airstream before winter",
     ["airstream", "?"], []),
    ("please delete the last paragraph and rewrite it to be more friendly",
     ["delete", "paragraph"], ["i've deleted", "here is", "here's"]),
    ("hey um can you look at the logs I think the player is crashing again "
     "you know after the update",
     ["logs", "crashing"], ["um ", "you know"]),
    ("tell them I'll be there at six scratch that at seven",
     ["seven"], ["six", "scratch"]),
    ("thanks for the update new paragraph I'll review the doc tonight",
     ["\n", "review"], ["new paragraph"]),
    ("What I saw was that the curtains were loading right you saw Oppenheimer "
     "logo Breathing which is great and then all sudden there's a transition "
     "where there's a blue black screen and then you see the actual video "
     "It's paused and then it's buffering and then it plays flawlessly",
     ["oppenheimer", "buffering", "flawlessly"], []),
]


def chat(model: str, user: str, num_predict: int) -> tuple[str, str, dict]:
    payload = {
        "model": model,
        "messages": ([{"role": "system",
                       "content": BASE_PROMPT + "\n" + TONE["default"]}]
                     + FEW_SHOT
                     + [{"role": "user", "content": user}]),
        "stream": False,
        "think": False,
        "keep_alive": "10m",
        "options": {"temperature": 0, "repeat_penalty": 1.0,
                    "num_predict": num_predict},
    }
    r = requests.post(f"{OLLAMA}/api/chat", json=payload, timeout=(5, 120))
    if r.status_code == 400 and "think" in r.text.lower():
        payload.pop("think")
        r = requests.post(f"{OLLAMA}/api/chat", json=payload, timeout=(5, 120))
    r.raise_for_status()
    d = r.json()
    out = re.sub(r"<think>.*?</think>", "", d["message"]["content"],
                 flags=re.S).strip().strip('"').strip()
    return out, d.get("done_reason", "stop"), d


def guard_reject(raw: str, out: str, done: str) -> str | None:
    """Mirror of llm_clean's output guard."""
    if not out or done == "length":
        return "empty/truncated"
    if REFUSAL_RE.match(out) and not REFUSAL_RE.match(raw.strip()):
        return "refusal/answer"
    if (len(out.split()) < 0.5 * len(raw.split())
            and "scratch that" not in raw.lower()):
        return "over-deletion"
    return None


def run_model(model: str, replays: list[str]):
    chat(model, "hi", 8)                                  # load & warm
    results, walls, rates = [], [], []

    for raw, need, forbid in ADVERSARIAL:
        t0 = time.time()
        try:
            out, done, d = chat(model, raw,
                                max(96, int(len(raw.split()) * 2.5) + 32))
        except Exception as e:
            results.append(("LEAK", raw, f"<error: {e}>"))
            continue
        walls.append(time.time() - t0)
        if d.get("eval_duration"):
            rates.append(d["eval_count"] / (d["eval_duration"] / 1e9))

        rej = guard_reject(raw, out, done)
        if rej:
            results.append((f"fallback ({rej})", raw, out))
            continue
        low = out.lower()
        bad = ([f"missing {s!r}" for s in need if s.lower() not in low]
               + [f"contains {s!r}" for s in forbid if s.lower() in low])
        results.append(("pass" if not bad else f"LEAK ({'; '.join(bad)})",
                        raw, out))

    print(f"\n=== {model} ===")
    npass = sum(1 for s, _, _ in results if s == "pass")
    nfall = sum(1 for s, _, _ in results if s.startswith("fallback"))
    nleak = len(results) - npass - nfall
    print(f"adversarial: {npass} pass, {nfall} fallback, {nleak} LEAK | "
          f"median {statistics.median(walls):.2f}s, "
          f"gen {statistics.median(rates):.0f} tok/s"
          if walls and rates else "no timing data")
    for status, raw, out in results:
        if status != "pass":
            print(f"  [{status}]")
            print(f"    in:  {raw[:100]}")
            print(f"    out: {out[:100]!r}")

    for raw in replays:
        t0 = time.time()
        try:
            out, done, _ = chat(model, raw,
                                max(96, int(len(raw.split()) * 2.5) + 32))
        except Exception as e:
            out, done = f"<error: {e}>", "stop"
        rej = guard_reject(raw, out, done)
        tag = f" [would fallback: {rej}]" if rej else ""
        print(f"  replay {time.time() - t0:.2f}s{tag}: {out[:110]}")

    return npass, nfall, nleak, (statistics.median(walls) if walls else 0), \
        (statistics.median(rates) if rates else 0)


def main():
    installed = {m["name"] for m in
                 requests.get(f"{OLLAMA}/api/tags", timeout=5)
                 .json()["models"]}
    models = sys.argv[1:] or [m for m in DEFAULT_CANDIDATES if m in installed]
    if not models:
        sys.exit("no candidate models installed")

    replays = []
    if TRANSCRIPTS_FILE.exists():
        for line in TRANSCRIPTS_FILE.read_text().splitlines()[-REPLAY_N:]:
            try:
                replays.append(json.loads(line)["raw"])
            except Exception:
                continue

    summary = {}
    for model in models:
        try:
            summary[model] = run_model(model, replays)
        except Exception as e:
            print(f"\n=== {model} ===\n  failed: {e}")

    print(f"\n{'model':<16} {'pass':>4} {'fallback':>8} {'LEAK':>5} "
          f"{'median':>7} {'tok/s':>6}")
    for m, (p, f, l, wall, rate) in summary.items():
        print(f"{m:<16} {p:>4} {f:>8} {l:>5} {wall:>6.2f}s {rate:>6.0f}")


if __name__ == "__main__":
    main()
