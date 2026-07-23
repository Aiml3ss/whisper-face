# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

"""Offline coverage for the git-based, opt-in self-updater.

Every case injects a fake ``subprocess.run``-style runner, so nothing here
touches the network, git, or the filesystem. The checkout path is a plain
sentinel: the fake matches on ``git -C <checkout> ...`` argv and never reads
``cwd``.
"""

import subprocess
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import self_update  # noqa: E402

CHECKOUT = Path("/whisper-face/checkout")
CURRENT = "a" * 40
LATEST = "b" * 40


class FakeRunner:
    """A subprocess.run-style callable with scripted git/installer results.

    Records every call so tests can assert ordering and that the checkout never
    ends on a broken revision. ``checkouts`` is the ordered list of revisions
    passed to ``git checkout``; ``installer_runs`` counts installer invocations.
    """

    def __init__(self, *, head="", upstream=None, upstream_ref="@{u}",
                 status="", fetch_rc=0, count=0,
                 checkout_rc=0, installer_rcs=None):
        self.head = head
        self.upstream = upstream
        self.upstream_ref = upstream_ref
        self.status = status
        self.fetch_rc = fetch_rc
        self.count = count
        self.checkout_rc = checkout_rc
        self.installer_rcs = list(installer_rcs or [])
        self.calls = []
        self.checkouts = []
        self.installer_runs = 0

    @staticmethod
    def _cp(cmd, rc, out="", err=""):
        return subprocess.CompletedProcess(list(cmd), rc, out, err)

    def __call__(self, cmd, *, capture_output=True, text=True,
                 timeout=None, cwd=None):
        self.calls.append(tuple(cmd))
        # Installer runs from the checkout dir via a relative path.
        if list(cmd) == ["./Install.command"]:
            self.installer_runs += 1
            rc = self.installer_rcs.pop(0) if self.installer_rcs else 0
            return self._cp(cmd, rc)
        # Everything else is `git -C <checkout> ...`.
        assert list(cmd[:2]) == ["git", "-C"], cmd
        git = list(cmd[3:])
        if git[:3] == ["rev-parse", "--verify", "--quiet"]:
            ref = git[3]
            if ref == "HEAD":
                return self._cp(cmd, 0, self.head + "\n") if self.head \
                    else self._cp(cmd, 1)
            if self.upstream is not None and ref == self.upstream_ref:
                return self._cp(cmd, 0, self.upstream + "\n")
            return self._cp(cmd, 1)  # upstream ref does not resolve
        if git == ["status", "--porcelain"]:
            return self._cp(cmd, 0, self.status)
        if git[:2] == ["fetch", "--quiet"]:
            return self._cp(cmd, self.fetch_rc)
        if git[:2] == ["rev-list", "--count"]:
            return self._cp(cmd, 0, f"{self.count}\n")
        if git[:2] == ["checkout", "--quiet"]:
            self.checkouts.append(git[2])
            return self._cp(cmd, self.checkout_rc)
        raise AssertionError(f"unexpected command: {cmd}")


class CheckForUpdateTests(unittest.TestCase):
    def test_up_to_date_reports_not_available(self):
        runner = FakeRunner(head=CURRENT, upstream=CURRENT)
        report = self_update.check_for_update(CHECKOUT, runner=runner)
        self.assertFalse(report["available"])
        self.assertEqual(report["current"], CURRENT)
        self.assertEqual(report["latest"], CURRENT)
        self.assertEqual(report["behind"], 0)
        self.assertIsNone(report["error"])
        # No commit-count needed when HEAD already equals upstream.
        self.assertFalse(any(c[3:5] == ("rev-list", "--count")
                             for c in runner.calls))

    def test_behind_reports_available_with_shas_and_count(self):
        runner = FakeRunner(head=CURRENT, upstream=LATEST, count=3)
        report = self_update.check_for_update(CHECKOUT, runner=runner)
        self.assertTrue(report["available"])
        self.assertEqual(report["current"], CURRENT)
        self.assertEqual(report["latest"], LATEST)
        self.assertEqual(report["behind"], 3)
        self.assertIsNone(report["error"])
        # It fetched exactly once and compared HEAD against the upstream sha.
        self.assertEqual(
            sum(1 for c in runner.calls if c[3:5] == ("fetch", "--quiet")), 1)
        self.assertIn(("git", "-C", str(CHECKOUT), "rev-list", "--count",
                       f"HEAD..{LATEST}"), runner.calls)

    def test_fetch_failure_fails_closed(self):
        runner = FakeRunner(head=CURRENT, upstream=LATEST, fetch_rc=1)
        report = self_update.check_for_update(CHECKOUT, runner=runner)
        self.assertFalse(report["available"])
        self.assertEqual(report["current"], CURRENT)
        self.assertEqual(report["latest"], "")
        self.assertIsNotNone(report["error"])
        self.assertIn("fetch", report["error"])
        # Never claimed an update: it stopped right after the failed fetch.
        self.assertFalse(any(c[3:5] == ("rev-list", "--count")
                             for c in runner.calls))

    def test_dirty_tree_refuses_without_touching_network(self):
        runner = FakeRunner(head=CURRENT, upstream=LATEST, status=" M dictate.py\n")
        report = self_update.check_for_update(CHECKOUT, runner=runner)
        self.assertFalse(report["available"])
        self.assertEqual(report["current"], CURRENT)
        self.assertEqual(report["error"], "local changes present")
        # A dirty tree must not reach the network.
        self.assertFalse(any("fetch" in c for c in runner.calls))

    def test_missing_repo_returns_clean_error(self):
        runner = FakeRunner(head="")  # rev-parse HEAD fails -> not a repo
        report = self_update.check_for_update(CHECKOUT, runner=runner)
        self.assertFalse(report["available"])
        self.assertEqual(report["error"], "not a git checkout")

    def test_runner_exception_never_raises(self):
        def boom(*a, **k):
            raise FileNotFoundError("git")
        report = self_update.check_for_update(CHECKOUT, runner=boom)
        self.assertFalse(report["available"])
        self.assertEqual(report["error"], "git is not installed")


class ApplyUpdateTests(unittest.TestCase):
    def test_happy_path_checks_out_then_installs_in_order(self):
        runner = FakeRunner(head=CURRENT, checkout_rc=0, installer_rcs=[0])
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner)
        self.assertEqual(outcome["status"], "applied")
        self.assertEqual(outcome["from"], CURRENT)
        self.assertEqual(outcome["to"], LATEST)
        self.assertEqual(runner.checkouts, [LATEST])
        self.assertEqual(runner.installer_runs, 1)
        # Ordered: record HEAD, checkout target, run installer.
        self.assertEqual(runner.calls[0][3:], ("rev-parse", "--verify",
                                               "--quiet", "HEAD"))
        self.assertEqual(runner.calls[1][3:], ("checkout", "--quiet", LATEST))
        self.assertEqual(runner.calls[2], ("./Install.command",))

    def test_installer_failure_rolls_back_and_never_ends_broken(self):
        # Forward install fails; rollback re-install succeeds.
        runner = FakeRunner(head=CURRENT, checkout_rc=0, installer_rcs=[1, 0])
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner)
        self.assertEqual(outcome["status"], "rolled_back")
        self.assertEqual(outcome["from"], CURRENT)
        self.assertEqual(outcome["to"], LATEST)
        self.assertIsNotNone(outcome["error"])
        # Checked out the target, then restored the previous revision.
        self.assertEqual(runner.checkouts, [LATEST, CURRENT])
        self.assertEqual(runner.checkouts[-1], CURRENT)  # never left broken
        self.assertEqual(runner.installer_runs, 2)

    def test_checkout_failure_stays_on_previous(self):
        # git refuses the checkout: nothing moved, installer must never run.
        runner = FakeRunner(head=CURRENT, checkout_rc=1)
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["from"], CURRENT)
        self.assertIsNotNone(outcome["error"])
        self.assertEqual(runner.checkouts, [LATEST])
        self.assertEqual(runner.installer_runs, 0)

    def test_missing_repo_returns_clean_failure(self):
        runner = FakeRunner(head="")
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error"], "not a git checkout")
        self.assertEqual(runner.checkouts, [])
        self.assertEqual(runner.installer_runs, 0)


if __name__ == "__main__":
    unittest.main()
