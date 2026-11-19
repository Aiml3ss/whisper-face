"""A fake dictation app for tests/test_outside_probe.py.

It stands where a real app would: it is told what it "hears" and when the
capture key goes down and up (on stdin, from the test's fake key driver and
fake player, never from a real key or a microphone), and after a known delay
it types the take's text into the probe's hidden test window, in two parts,
through that window's echo pipe. It logs the moment it sends each part, on
the clock the probe uses, so the test can compare the probe's timestamps
with the truth.

    python fake_dictation_target.py --fifo PATH --texts JSON \\
        --first-delay-ms 150 --final-delay-ms 450
"""

import argparse
import json
import sys
import time


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--fifo", required=True)
    parser.add_argument("--texts", required=True,
                        help="a JSON file mapping take id to its text")
    parser.add_argument("--first-delay-ms", type=int, required=True)
    parser.add_argument("--final-delay-ms", type=int, required=True)
    args = parser.parse_args()
    with open(args.texts, encoding="utf-8") as handle:
        texts = json.load(handle)

    def log(**fields) -> None:
        sys.stdout.write(json.dumps(fields) + "\n")
        sys.stdout.flush()

    heard = None
    with open(args.fifo, "w", encoding="utf-8") as window:
        log(event="ready")
        for line in sys.stdin:
            word, _, rest = line.strip().partition(" ")
            if word == "hear":
                heard = rest
            elif word == "down":
                log(event="down", t=time.monotonic_ns())
            elif word == "up":
                released = time.monotonic_ns()
                log(event="up", t=released)
                text = texts.get(heard, "")
                cut = text.find(" ", len(text) // 2)
                parts = [text[:cut], text[cut:]] if cut > 0 else [text, ""]
                for part, delay in zip(parts, (args.first_delay_ms,
                                               args.final_delay_ms)):
                    wake = released + delay * 1_000_000
                    while time.monotonic_ns() < wake:
                        time.sleep(0.001)
                    if not part:
                        continue
                    sent = time.monotonic_ns()
                    window.write(json.dumps({"type": part}) + "\n")
                    window.flush()
                    log(event="sent", take=heard, t=sent, text=part)
                heard = None
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
