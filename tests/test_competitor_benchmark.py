import json
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from competitor_benchmark import (  # noqa: E402
    EVIDENCE_SCOPE,
    Protocol,
    ProtocolError,
    evaluate_product_run,
    evaluate_product_runs,
    main,
)


CORPUS_PATH = ROOT / "benchmarks" / "competitor_tasks.json"


def observation(
    task_id,
    state="measured",
    *,
    completed=True,
    errors=0,
    latency=1000,
    interactions=1,
    reason=None,
    reference="artifacts/run.json",
):
    if state != "measured":
        completed = errors = latency = interactions = None
    if state == "unavailable":
        reference = None
    return {
        "task_id": task_id,
        "evidence_state": state,
        "completed": completed,
        "error_count": errors,
        "latency_ms": latency,
        "interaction_count": interactions,
        "unavailable_reason": reason,
        "source_reference": reference,
    }


def product_run(protocol, observations, *, product="product-a", run_id="run-1"):
    return {
        "schema_version": 1,
        "protocol_id": protocol.protocol_id,
        "product_id": product,
        "run_id": run_id,
        "environment_id": "mac-test-fixture",
        "observations": observations,
    }


class CompetitorBenchmarkTests(unittest.TestCase):
    def setUp(self):
        self.corpus = json.loads(CORPUS_PATH.read_text())
        self.protocol = Protocol.from_mapping(self.corpus)

    def test_corpus_is_neutral_and_contains_no_product_results(self):
        self.assertEqual(self.corpus["evidence_scope"], EVIDENCE_SCOPE)
        self.assertIs(self.corpus["product_results_included"], False)
        self.assertEqual(len(self.protocol.tasks), 6)
        serialized = json.dumps(self.corpus).casefold()
        for product_name in ("whisper face", "wispr flow", "openwhisper"):
            self.assertNotIn(product_name, serialized)

    def test_measured_observations_compute_descriptive_aggregates(self):
        observations = [
            observation(task.task_id, completed=index != 1, errors=index,
                        latency=1000 + index * 100, interactions=index + 1)
            for index, task in enumerate(self.protocol.tasks)
        ]

        result = evaluate_product_run(
            self.protocol, product_run(self.protocol, observations)
        )

        self.assertEqual(result["coverage"], {
            "tasks": 6, "measured": 6, "unavailable": 0, "claimed_only": 0,
        })
        self.assertEqual(result["measured"], {
            "completed_tasks": 5,
            "completion_rate": 0.8333,
            "error_count_total": 15,
            "per_task": [
                {
                    "task_id": task.task_id,
                    "completed": index != 1,
                    "error_count": index,
                    "latency_ms": float(1000 + index * 100),
                    "interaction_count": index + 1,
                }
                for index, task in sorted(
                    enumerate(self.protocol.tasks),
                    key=lambda entry: entry[1].task_id,
                )
            ],
        })

    def test_unavailable_and_claimed_only_never_enter_measured_aggregates(self):
        task_ids = [task.task_id for task in self.protocol.tasks]
        observations = [
            observation(task_ids[0], completed=True, errors=1, latency=900),
            observation(task_ids[1], state="claimed_only",
                        reference="vendor-page-snapshot"),
            observation(task_ids[2], state="unavailable", reason="not_run"),
            observation(task_ids[3], state="unavailable",
                        reason="environment_unsupported"),
            observation(task_ids[4], completed=False, errors=2, latency=1100),
            observation(task_ids[5], completed=True, errors=0, latency=1000),
        ]

        result = evaluate_product_run(
            self.protocol, product_run(self.protocol, observations)
        )

        self.assertEqual(result["coverage"], {
            "tasks": 6, "measured": 3, "unavailable": 2, "claimed_only": 1,
        })
        self.assertEqual(result["measured"]["completion_rate"], 0.6667)
        self.assertEqual(result["measured"]["error_count_total"], 3)
        self.assertEqual(
            [item["latency_ms"] for item in result["measured"]["per_task"]],
            [900.0, 1100.0, 1000.0],
        )
        self.assertEqual(result["unavailable"]["reasons"], {
            "environment_unsupported": 1, "not_run": 1,
        })
        self.assertIs(
            result["claimed_only"]["included_in_measured_aggregates"], False
        )

    def test_nonmeasured_records_cannot_smuggle_measurements(self):
        task_id = self.protocol.tasks[0].task_id
        claimed = observation(task_id, state="claimed_only", reference="claim")
        claimed["latency_ms"] = 10
        unavailable = observation(task_id, state="unavailable", reason="not_run")
        unavailable["completed"] = True

        for record in (claimed, unavailable):
            run = product_run(
                self.protocol,
                [record] + [
                    observation(task.task_id)
                    for task in self.protocol.tasks[1:]
                ],
            )
            with self.assertRaisesRegex(ProtocolError, "cannot carry"):
                evaluate_product_run(self.protocol, run)

    def test_run_requires_exactly_one_explicit_observation_per_task(self):
        observations = [observation(task.task_id) for task in self.protocol.tasks]
        with self.assertRaisesRegex(ProtocolError, "cover every"):
            evaluate_product_run(
                self.protocol, product_run(self.protocol, observations[:-1])
            )
        with self.assertRaisesRegex(ProtocolError, "exactly one"):
            evaluate_product_run(
                self.protocol,
                product_run(self.protocol, observations + [observations[0]]),
            )

    def test_protocol_and_observation_schemas_are_closed(self):
        with self.assertRaisesRegex(ProtocolError, "protocol corpus schema"):
            Protocol.from_mapping({**self.corpus, "winner": "product-a"})
        changed = dict(self.corpus)
        changed["measurement_fields"] = [
            *self.corpus["measurement_fields"], "marketing_score",
        ]
        with self.assertRaisesRegex(ProtocolError, "measurement fields"):
            Protocol.from_mapping(changed)
        changed = dict(self.corpus)
        changed["measurement_definitions"] = {
            **self.corpus["measurement_definitions"],
            "latency_ms": "Start whenever you want.",
        }
        with self.assertRaisesRegex(ProtocolError, "measurement definitions"):
            Protocol.from_mapping(changed)

        observations = [observation(task.task_id) for task in self.protocol.tasks]
        observations[0] = {**observations[0], "claimed_accuracy": 0.99}
        with self.assertRaisesRegex(ProtocolError, "observation schema"):
            evaluate_product_run(
                self.protocol, product_run(self.protocol, observations)
            )

    def test_multiple_runs_are_stable_and_have_no_ranking_fields(self):
        observations = [observation(task.task_id) for task in self.protocol.tasks]
        runs = [
            product_run(self.protocol, observations, product="zeta", run_id="run-2"),
            product_run(self.protocol, observations, product="alpha", run_id="run-1"),
        ]

        results = evaluate_product_runs(self.protocol, runs)

        self.assertEqual([item["product_id"] for item in results], ["alpha", "zeta"])
        serialized = json.dumps(results)
        for forbidden in ("winner", "rank", "better", "best"):
            self.assertNotIn(forbidden, serialized)

    def test_zero_measured_tasks_produces_none_not_invented_metrics(self):
        observations = [
            observation(task.task_id, state="unavailable", reason="not_run")
            for task in self.protocol.tasks
        ]

        result = evaluate_product_run(
            self.protocol, product_run(self.protocol, observations)
        )

        self.assertEqual(result["coverage"]["measured"], 0)
        self.assertEqual(result["measured"], {
            "completed_tasks": 0,
            "completion_rate": None,
            "error_count_total": None,
            "per_task": [],
        })

    def test_incomplete_measured_task_requires_an_error(self):
        observations = [
            observation(task.task_id) for task in self.protocol.tasks
        ]
        observations[0] = observation(
            self.protocol.tasks[0].task_id, completed=False, errors=0)
        with self.assertRaisesRegex(ProtocolError, "requires at least one"):
            evaluate_product_run(
                self.protocol, product_run(self.protocol, observations))

    def test_cli_evaluates_run_files_without_ranking(self):
        import tempfile
        from contextlib import redirect_stdout
        from io import StringIO

        observations = [
            observation(task.task_id, state="unavailable", reason="not_run")
            for task in self.protocol.tasks
        ]
        run = product_run(self.protocol, observations)
        with tempfile.TemporaryDirectory() as directory:
            run_path = Path(directory) / "run.json"
            run_path.write_text(json.dumps(run), encoding="utf-8")
            output = StringIO()
            with redirect_stdout(output):
                self.assertEqual(main([
                    "--protocol", str(CORPUS_PATH), str(run_path),
                ]), 0)

        payload = json.loads(output.getvalue())
        self.assertEqual(payload[0]["coverage"]["tasks"], 6)
        self.assertNotIn("rank", output.getvalue().casefold())


if __name__ == "__main__":
    unittest.main()
