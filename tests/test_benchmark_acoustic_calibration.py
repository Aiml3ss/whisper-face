# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

from contextlib import redirect_stdout
import io
import json
from pathlib import Path
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from benchmark_acoustic_calibration import (  # noqa: E402
    main,
    run_synthetic_benchmark,
)


class AcousticCalibrationSyntheticBenchmarkTests(unittest.TestCase):
    def test_report_has_keep_kill_and_insufficient_evidence_without_claims(self):
        report = run_synthetic_benchmark()

        self.assertEqual(report["matched"], report["cases"])
        self.assertEqual(report["counts"], {
            "keep": 1,
            "kill": 2,
            "insufficient-evidence": 4,
        })
        self.assertEqual(
            report["evidence_scope"],
            "deterministic-policy-conformance-only",
        )
        self.assertFalse(report["activation_claim"])
        self.assertFalse(report["quality_claim"])
        self.assertFalse(report["physical_evidence"])
        encoded = json.dumps(report).casefold()
        for forbidden in ("audio", "transcript", "winner", "best quality"):
            self.assertNotIn(forbidden, encoded)

    def test_cli_is_deterministic_json_and_passes(self):
        first = io.StringIO()
        second = io.StringIO()
        with redirect_stdout(first):
            first_status = main([])
        with redirect_stdout(second):
            second_status = main([])

        self.assertEqual(first_status, 0)
        self.assertEqual(second_status, 0)
        self.assertEqual(first.getvalue(), second.getvalue())
        self.assertEqual(json.loads(first.getvalue())["counts"]["kill"], 2)


if __name__ == "__main__":
    unittest.main()
