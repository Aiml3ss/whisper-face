# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import tempfile
import unittest
from pathlib import Path
import sys

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark_asr import (
    edit_distance,
    evenly_spaced,
    load_references,
    score_records,
)


class BenchmarkASRTests(unittest.TestCase):
    def test_evenly_spaced_selection_is_stable_and_includes_endpoints(self):
        items = [Path(str(index)) for index in range(10)]
        self.assertEqual(
            evenly_spaced(items, 4),
            [Path("0"), Path("3"), Path("6"), Path("9")],
        )
        self.assertEqual(evenly_spaced(items, 1), [Path("0")])
        self.assertEqual(evenly_spaced(items, 0), [])

    def test_load_references_uses_librispeech_layout(self):
        with tempfile.TemporaryDirectory() as directory:
            chapter = Path(directory) / "1" / "2"
            chapter.mkdir(parents=True)
            (chapter / "1-2.trans.txt").write_text(
                "1-2-0000 HELLO WORLD\n1-2-0001 SECOND TEST\n",
                encoding="utf-8",
            )
            self.assertEqual(load_references(Path(directory)), {
                "1-2-0000": "HELLO WORLD",
                "1-2-0001": "SECOND TEST",
            })

    def test_edit_distance_handles_insert_delete_and_substitute(self):
        self.assertEqual(edit_distance("a b c".split(), "a x c".split()), 1)
        self.assertEqual(edit_distance("a b".split(), "a b c".split()), 1)
        self.assertEqual(edit_distance("a b c".split(), "a c".split()), 1)

    def test_score_records_uses_one_tokenizer_for_every_engine(self):
        tokenize = lambda text: text.lower().replace("!", "").split()
        result = score_records([
            {
                "engine": "candidate",
                "ref": "Hello world!",
                "hyp": "hello world",
                "audio_s": 2.0,
                "proc_s": 0.5,
            },
            {
                "engine": "candidate",
                "ref": "one two",
                "hyp": "one too",
                "audio_s": 1.0,
                "proc_s": 0.25,
            },
        ], tokenize=tokenize)
        self.assertEqual(result["utterances"], 2)
        self.assertEqual(result["wer_pct"], 25.0)
        self.assertEqual(result["exact_pct"], 50.0)
        self.assertEqual(result["rtfx"], 4.0)


if __name__ == "__main__":
    unittest.main()
