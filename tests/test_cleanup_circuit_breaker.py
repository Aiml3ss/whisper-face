# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from cleanup_circuit_breaker import (  # noqa: E402
    AdmissionState,
    CleanupCircuitBreaker,
)


class FakeClock:
    def __init__(self) -> None:
        self.now = 100.0

    def __call__(self) -> float:
        return self.now


class CleanupCircuitBreakerTests(unittest.TestCase):
    def setUp(self):
        self.clock = FakeClock()
        self.breaker = CleanupCircuitBreaker(
            cooldown_seconds=60.0, clock=self.clock)

    def test_transport_failure_opens_bounded_content_free_cooldown(self):
        self.assertTrue(self.breaker.acquire().allowed)

        self.breaker.record_transport_failure()
        receipt = self.breaker.acquire()

        self.assertEqual(receipt.state, AdmissionState.COOLDOWN)
        self.assertEqual(receipt.retry_after_ms, 60_000)
        self.assertEqual(set(receipt.__dataclass_fields__), {
            "state", "retry_after_ms"})

    def test_cooldown_allows_exactly_one_probe_then_success_closes(self):
        self.breaker.acquire()
        self.breaker.record_transport_failure()
        self.clock.now += 60.0

        probe = self.breaker.acquire()
        concurrent = self.breaker.acquire()
        self.assertTrue(probe.allowed)
        self.assertEqual(concurrent.state, AdmissionState.IN_FLIGHT)

        self.breaker.record_success()
        self.assertTrue(self.breaker.acquire().allowed)
        self.breaker.release()

    def test_non_transport_rejection_releases_without_cooldown(self):
        self.breaker.acquire()
        self.breaker.release()

        self.assertTrue(self.breaker.acquire().allowed)
        self.breaker.record_success()

    def test_invalid_configuration_and_transition_fail_closed(self):
        for value in (False, 0, 301, float("nan")):
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    CleanupCircuitBreaker(cooldown_seconds=value)
        with self.assertRaises(TypeError):
            CleanupCircuitBreaker(clock=None)
        with self.assertRaises(RuntimeError):
            self.breaker.record_success()
        with self.assertRaises(RuntimeError):
            self.breaker.record_transport_failure()


if __name__ == "__main__":
    unittest.main()
