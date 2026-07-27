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

import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

import self_update  # noqa: E402

CHECKOUT = Path("/whisper-face/checkout")
CURRENT = "a" * 40
LATEST = "b" * 40
INSTALLED = "c" * 40

# Every check here names a receipt explicitly. Left to its default,
# check_for_update would read the real one out of the running user's home
# directory, which is exactly the kind of ambient dependency this suite exists
# to avoid.
NO_RECEIPT = Path("/whisper-face/nonexistent/launcher-install.json")


class FakeRunner:
    """A subprocess.run-style callable with scripted git/installer results.

    Records every call so tests can assert ordering and that the checkout never
    ends on a broken revision. ``checkouts`` is the ordered list of revisions
    passed to ``git checkout``; ``installer_runs`` counts installer invocations.
    """

    def __init__(self, *, head="", upstream=None, upstream_ref="@{u}",
                 status="", fetch_rc=0, count=0,
                 checkout_rc=0, checkout_rcs=None, installer_rcs=None,
                 branch="", branch_sha=None, resolvable=(), ancestry=None,
                 installer_stdout="", installer_stderr=""):
        # Default ancestry: HEAD is simply behind upstream, which is the shape
        # every pre-existing case assumes.
        if ancestry is None:
            ancestry = {(head, upstream)} if head and upstream else set()
        self.head = head
        self.upstream = upstream
        self.upstream_ref = upstream_ref
        self.status = status
        self.fetch_rc = fetch_rc
        self.count = count
        self.checkout_rc = checkout_rc
        # Per-call checkout results, popped like installer_rcs; a failing
        # rollback checkout is a different event than a failing forward one.
        self.checkout_rcs = list(checkout_rcs or [])
        self.installer_rcs = list(installer_rcs or [])
        # "" models a detached HEAD, which is what `symbolic-ref` reports by
        # refusing. `branch_sha` defaults to HEAD: an update moves HEAD alone,
        # so the branch it started on still points at the pre-update commit.
        self.branch = branch
        self.branch_sha = head if branch_sha is None else branch_sha
        # Revisions this repo knows about, for `<sha>^{commit}` lookups. A sha
        # left out of this set models a receipt naming a commit the checkout has
        # never fetched.
        self.resolvable = set(resolvable)
        # (older, newer) pairs `git merge-base --is-ancestor` accepts. Anything
        # not listed is treated as unrelated or newer, which is how a diverged
        # or locally-ahead checkout is modelled.
        self.ancestry = set(ancestry)
        self.installer_stdout = installer_stdout
        self.installer_stderr = installer_stderr
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
            return self._cp(cmd, rc, self.installer_stdout,
                            self.installer_stderr)
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
            if self.branch and ref == self.branch:
                return self._cp(cmd, 0, self.branch_sha + "\n")
            if ref.endswith("^{commit}"):
                sha = ref[: -len("^{commit}")]
                if sha in self.resolvable:
                    return self._cp(cmd, 0, sha + "\n")
                return self._cp(cmd, 1)  # unknown object
            return self._cp(cmd, 1)  # upstream ref does not resolve
        if git[:2] == ["merge-base", "--is-ancestor"]:
            older, newer = git[2], git[3]
            same_or_known = older == newer or (older, newer) in self.ancestry
            return self._cp(cmd, 0 if same_or_known else 1)
        if git == ["symbolic-ref", "--short", "--quiet", "HEAD"]:
            return self._cp(cmd, 0, self.branch + "\n") if self.branch \
                else self._cp(cmd, 1)  # detached HEAD
        if git == ["status", "--porcelain"]:
            return self._cp(cmd, 0, self.status)
        if git[:2] == ["fetch", "--quiet"]:
            return self._cp(cmd, self.fetch_rc)
        if git[:2] == ["rev-list", "--count"]:
            return self._cp(cmd, 0, f"{self.count}\n")
        if git[:2] == ["checkout", "--quiet"]:
            self.checkouts.append(git[2])
            rc = self.checkout_rcs.pop(0) if self.checkout_rcs \
                else self.checkout_rc
            if rc == 0:
                # Track HEAD through the checkout, as git would: a branch
                # name lands on the commit the branch points at, anything
                # else is taken as a revision. Without this, _restore's
                # verification would compare against a stale HEAD.
                target = git[2]
                self.head = self.branch_sha if target == self.branch \
                    else target
            return self._cp(cmd, rc)
        raise AssertionError(f"unexpected command: {cmd}")


class CheckForUpdateTests(unittest.TestCase):
    def test_up_to_date_reports_not_available(self):
        runner = FakeRunner(head=CURRENT, upstream=CURRENT)
        report = self_update.check_for_update(
            CHECKOUT, runner=runner, install_receipt=NO_RECEIPT)
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
        report = self_update.check_for_update(
            CHECKOUT, runner=runner, install_receipt=NO_RECEIPT)
        self.assertTrue(report["available"])
        self.assertEqual(report["current"], CURRENT)
        self.assertEqual(report["latest"], LATEST)
        self.assertEqual(report["behind"], 3)
        self.assertIsNone(report["error"])
        # It fetched exactly once and compared HEAD against the upstream sha.
        self.assertEqual(
            sum(1 for c in runner.calls if c[3:5] == ("fetch", "--quiet")), 1)
        self.assertIn(("git", "-C", str(CHECKOUT), "rev-list", "--count",
                       f"{CURRENT}..{LATEST}"), runner.calls)

    def test_fetch_failure_fails_closed(self):
        runner = FakeRunner(head=CURRENT, upstream=LATEST, fetch_rc=1)
        report = self_update.check_for_update(
            CHECKOUT, runner=runner, install_receipt=NO_RECEIPT)
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
        report = self_update.check_for_update(
            CHECKOUT, runner=runner, install_receipt=NO_RECEIPT)
        self.assertFalse(report["available"])
        self.assertEqual(report["current"], CURRENT)
        self.assertEqual(report["error"], "local changes present")
        # A dirty tree must not reach the network.
        self.assertFalse(any("fetch" in c for c in runner.calls))

    def test_missing_repo_returns_clean_error(self):
        runner = FakeRunner(head="")  # rev-parse HEAD fails -> not a repo
        report = self_update.check_for_update(
            CHECKOUT, runner=runner, install_receipt=NO_RECEIPT)
        self.assertFalse(report["available"])
        self.assertEqual(report["error"], "not a git checkout")

    def test_runner_exception_never_raises(self):
        def boom(*a, **k):
            raise FileNotFoundError("git")
        report = self_update.check_for_update(
            CHECKOUT, runner=boom, install_receipt=NO_RECEIPT)
        self.assertFalse(report["available"])
        self.assertEqual(report["error"], "git is not installed")


class InstalledRevisionTests(unittest.TestCase):
    """The check must answer about the app that is running, not the files.

    The app runs from a git checkout, so anything that moves that checkout --
    a developer pulling, a script, a rolled-back update -- leaves HEAD ahead of
    the build the user actually launched. Comparing HEAD against upstream then
    reports "up to date" to someone running a demonstrably older app, and the
    only way out is a manual reinstall they have no reason to suspect.
    """

    def receipt(self, payload) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "launcher-install.json"
        path.write_text(
            payload if isinstance(payload, str) else json.dumps(payload),
            encoding="utf-8")
        self.addCleanup(directory.rmdir)
        self.addCleanup(path.unlink)
        return path

    def test_a_checkout_moved_behind_the_apps_back_still_offers_it(self):
        # HEAD already sits at upstream -- someone pulled -- but the installer
        # last provisioned INSTALLED, so that is the app the user is running.
        runner = FakeRunner(head=LATEST, upstream=LATEST, count=6,
                            resolvable={INSTALLED},
                            ancestry={(INSTALLED, LATEST)})
        report = self_update.check_for_update(
            CHECKOUT, runner=runner,
            install_receipt=self.receipt({"source_revision": INSTALLED}))

        self.assertTrue(report["available"])
        self.assertEqual(report["current"], INSTALLED)
        self.assertEqual(report["latest"], LATEST)
        self.assertEqual(report["behind"], 6)
        self.assertIsNone(report["error"])
        # Counted from what is installed, never from HEAD.
        self.assertIn(("git", "-C", str(CHECKOUT), "rev-list", "--count",
                       f"{INSTALLED}..{LATEST}"), runner.calls)

    def test_installed_revision_at_upstream_is_up_to_date(self):
        runner = FakeRunner(head=LATEST, upstream=LATEST, resolvable={LATEST})
        report = self_update.check_for_update(
            CHECKOUT, runner=runner,
            install_receipt=self.receipt({"source_revision": LATEST}))
        self.assertFalse(report["available"])
        self.assertEqual(report["current"], LATEST)
        self.assertEqual(report["behind"], 0)

    def test_absent_receipt_falls_back_to_head(self):
        runner = FakeRunner(head=CURRENT, upstream=LATEST, count=3)
        report = self_update.check_for_update(
            CHECKOUT, runner=runner, install_receipt=NO_RECEIPT)
        self.assertTrue(report["available"])
        self.assertEqual(report["current"], CURRENT)

    def test_unreadable_receipt_falls_back_to_head(self):
        for payload in ("not json at all", "[]", '{"source_revision": 12}',
                        '{"source_revision": ""}', "{}"):
            with self.subTest(payload=payload):
                runner = FakeRunner(head=CURRENT, upstream=LATEST, count=3)
                report = self_update.check_for_update(
                    CHECKOUT, runner=runner,
                    install_receipt=self.receipt(payload))
                self.assertEqual(report["current"], CURRENT)
                self.assertTrue(report["available"])

    def test_receipt_naming_an_unknown_commit_falls_back_to_head(self):
        # A revision this checkout has never fetched must not poison the
        # comparison; git refuses to resolve it and HEAD is used instead.
        runner = FakeRunner(head=CURRENT, upstream=LATEST, count=3,
                            resolvable=set())
        report = self_update.check_for_update(
            CHECKOUT, runner=runner,
            install_receipt=self.receipt({"source_revision": INSTALLED}))
        self.assertEqual(report["current"], CURRENT)
        self.assertIn(("git", "-C", str(CHECKOUT), "rev-list", "--count",
                       f"{CURRENT}..{LATEST}"), runner.calls)

    def test_a_receipt_newer_than_head_is_ignored(self):
        """A checkout rolled *back* runs the older code, whatever was installed.

        Install B, check out the older A, let launchd restart the service: the
        process is running A while the receipt still names B. Trusting the
        receipt here would compare B against upstream and answer "up to date"
        to someone running A.
        """
        runner = FakeRunner(head=CURRENT, upstream=INSTALLED, count=4,
                            resolvable={INSTALLED},
                            ancestry={(CURRENT, INSTALLED)})
        report = self_update.check_for_update(
            CHECKOUT, runner=runner,
            install_receipt=self.receipt({"source_revision": INSTALLED}))
        self.assertTrue(report["available"])
        self.assertEqual(report["current"], CURRENT)
        self.assertEqual(report["behind"], 4)

    def test_a_checkout_with_its_own_commits_is_refused_not_rewound(self):
        """Never offer an "update" that would move a tree backwards.

        A clean checkout carrying local commits is not behind upstream; applying
        would `git checkout <upstream>` straight off that work. Fail closed and
        say so rather than counting from the receipt and calling it an update.
        """
        runner = FakeRunner(head=CURRENT, upstream=LATEST, count=9,
                            resolvable={INSTALLED}, ancestry=set())
        report = self_update.check_for_update(
            CHECKOUT, runner=runner,
            install_receipt=self.receipt({"source_revision": INSTALLED}))
        self.assertFalse(report["available"])
        self.assertEqual(report["error"], "checkout has commits upstream does not")
        self.assertFalse(any(c[3:5] == ("rev-list", "--count")
                             for c in runner.calls))

    def test_a_dirty_tree_still_refuses_before_the_network(self):
        runner = FakeRunner(head=LATEST, upstream=LATEST,
                            status=" M dictate.py\n", resolvable={INSTALLED})
        report = self_update.check_for_update(
            CHECKOUT, runner=runner,
            install_receipt=self.receipt({"source_revision": INSTALLED}))
        self.assertFalse(report["available"])
        self.assertEqual(report["error"], "local changes present")
        self.assertFalse(any("fetch" in c for c in runner.calls))


class ApplyRecoversTheWorkingBuildTests(unittest.TestCase):
    """Recovery has to land on a build that installed cleanly once.

    When the checkout has been moved forward without a reinstall, HEAD is the
    revision that has never been installed and the receipt names the one that
    has. Treating HEAD as the rollback target would "recover" onto the revision
    that just failed, and this update path has already produced real installer
    failures, so the difference is whether a failed update leaves a working app.
    """

    def receipt(self, revision) -> Path:
        directory = Path(tempfile.mkdtemp())
        path = directory / "launcher-install.json"
        path.write_text(json.dumps({"source_revision": revision}),
                        encoding="utf-8")
        self.addCleanup(directory.rmdir)
        self.addCleanup(path.unlink)
        return path

    def test_rollback_restores_the_installed_build_not_the_failing_head(self):
        # HEAD already sits at the target; only the installer needs to run, and
        # it fails. Recovery must go back to INSTALLED, not to LATEST.
        runner = FakeRunner(head=LATEST, checkout_rc=0, installer_rcs=[1, 0],
                            resolvable={INSTALLED},
                            ancestry={(INSTALLED, LATEST)})
        outcome = self_update.apply_update(
            CHECKOUT, LATEST, runner=runner,
            install_receipt=self.receipt(INSTALLED))

        self.assertEqual(outcome["status"], "rolled_back")
        self.assertEqual(outcome["from"], INSTALLED)
        self.assertEqual(runner.checkouts, [INSTALLED])
        self.assertEqual(runner.installer_runs, 2)

    def test_head_already_at_target_is_installed_without_detaching(self):
        runner = FakeRunner(head=LATEST, branch="main", installer_rcs=[0],
                            resolvable={INSTALLED},
                            ancestry={(INSTALLED, LATEST)})
        outcome = self_update.apply_update(
            CHECKOUT, LATEST, runner=runner,
            install_receipt=self.receipt(INSTALLED))

        self.assertEqual(outcome["status"], "applied")
        self.assertEqual(outcome["from"], INSTALLED)
        # No checkout at all: the files are already right, and `git checkout
        # <sha>` would have detached HEAD off `main` for nothing.
        self.assertEqual(runner.checkouts, [])
        self.assertEqual(runner.installer_runs, 1)

    def test_a_receipt_newer_than_head_is_not_a_rollback_target(self):
        runner = FakeRunner(head=CURRENT, checkout_rc=0, installer_rcs=[1, 0],
                            resolvable={INSTALLED},
                            ancestry={(CURRENT, INSTALLED)})
        outcome = self_update.apply_update(
            CHECKOUT, LATEST, runner=runner,
            install_receipt=self.receipt(INSTALLED))
        self.assertEqual(outcome["from"], CURRENT)
        self.assertEqual(runner.checkouts, [LATEST, CURRENT])


class RollbackIsVerifiedTests(unittest.TestCase):
    """"Rolled back" must mean the previous build is verifiably present.

    The old path ignored both the restore result and the rollback
    installer's exit code, then reported rolled_back unconditionally -- so
    a user whose recovery had actually failed was told their previous
    version was restored while the app may not have been running at all.
    """

    def test_a_failed_restore_is_reported_not_dressed_up(self):
        # Forward checkout succeeds, forward install fails, and every
        # rollback checkout fails too: HEAD is stuck on the broken revision.
        runner = FakeRunner(head=CURRENT, checkout_rcs=[0, 1, 1],
                            installer_rcs=[1, 0])
        outcome = self_update.apply_update(
            CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "rollback_failed")
        self.assertIn("could not restore revision", outcome["error"])
        self.assertIn(CURRENT[:7], outcome["error"])
        # The forward failure is still named alongside the recovery failure.
        self.assertIn("Install.command failed", outcome["error"])
        # And crucially, the installer never ran from the unrestored broken
        # revision: it can restart services and rewrite the install receipt
        # against exactly the build being rolled away from.
        self.assertEqual(runner.installer_runs, 1)

    def test_a_failed_rollback_reinstall_is_reported(self):
        # Restore succeeds but reinstalling the previous build fails: the
        # files are right and the services are not, which is not recovery.
        runner = FakeRunner(head=CURRENT, checkout_rc=0,
                            installer_rcs=[1, 1],
                            installer_stderr="pip exploded")
        outcome = self_update.apply_update(
            CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "rollback_failed")
        self.assertIn("recovery also failed", outcome["error"])
        self.assertIn("pip exploded", outcome["error"])

    def test_an_exception_path_keeps_the_recovery_reason(self):
        # A git timeout mid-apply, then a rollback whose reinstall fails:
        # the result must name both, not just the original exception --
        # recovery is the failure that now needs manual repair.
        inner = FakeRunner(head=CURRENT, installer_rcs=[1],
                           installer_stderr="pip exploded")
        state = {"raised": False}

        def flaky(cmd, **kwargs):
            git = list(cmd[3:]) if list(cmd[:2]) == ["git", "-C"] else []
            if git[:2] == ["checkout", "--quiet"] and not state["raised"]:
                state["raised"] = True
                raise subprocess.TimeoutExpired(cmd, 1)
            return inner(cmd, **kwargs)

        outcome = self_update.apply_update(
            CHECKOUT, LATEST, runner=flaky, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "rollback_failed")
        self.assertIn("timed out", outcome["error"])
        self.assertIn("recovery also failed", outcome["error"])
        self.assertIn("pip exploded", outcome["error"])

    def test_a_verified_rollback_still_reports_rolled_back(self):
        runner = FakeRunner(head=CURRENT, checkout_rc=0,
                            installer_rcs=[1, 0])
        outcome = self_update.apply_update(
            CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "rolled_back")
        # HEAD demonstrably came back to the previous revision.
        self.assertEqual(runner.head, CURRENT)

class ApplyUpdateTests(unittest.TestCase):
    def test_happy_path_checks_out_then_installs_in_order(self):
        runner = FakeRunner(head=CURRENT, checkout_rc=0, installer_rcs=[0])
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "applied")
        self.assertEqual(outcome["from"], CURRENT)
        self.assertEqual(outcome["to"], LATEST)
        self.assertEqual(runner.checkouts, [LATEST])
        self.assertEqual(runner.installer_runs, 1)
        # Ordered: record HEAD, record the branch it is on so a rollback can
        # return to it, checkout target, run installer.
        self.assertEqual(runner.calls[0][3:], ("rev-parse", "--verify",
                                               "--quiet", "HEAD"))
        self.assertEqual(runner.calls[1][3:], ("symbolic-ref", "--short",
                                               "--quiet", "HEAD"))
        self.assertEqual(runner.calls[2][3:], ("checkout", "--quiet", LATEST))
        self.assertEqual(runner.calls[3], ("./Install.command",))

    def test_installer_failure_rolls_back_and_never_ends_broken(self):
        # Forward install fails; rollback re-install succeeds.
        runner = FakeRunner(head=CURRENT, checkout_rc=0, installer_rcs=[1, 0])
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "rolled_back")
        self.assertEqual(outcome["from"], CURRENT)
        self.assertEqual(outcome["to"], LATEST)
        self.assertIsNotNone(outcome["error"])
        # Checked out the target, then restored the previous revision.
        self.assertEqual(runner.checkouts, [LATEST, CURRENT])
        self.assertEqual(runner.checkouts[-1], CURRENT)  # never left broken
        self.assertEqual(runner.installer_runs, 2)

    def test_rollback_restores_the_branch_not_a_detached_sha(self):
        # Regression: rolling back to the bare sha left the checkout on a
        # detached HEAD, so afterwards `git pull` and upstream tracking were
        # gone and the user's tree was silently off its branch.
        runner = FakeRunner(head=CURRENT, branch="main",
                            installer_rcs=[1, 0])
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "rolled_back")
        self.assertEqual(runner.checkouts, [LATEST, "main"])

    def test_rollback_uses_the_sha_when_head_was_already_detached(self):
        # No branch to go back to: the sha remains the only correct target.
        runner = FakeRunner(head=CURRENT, branch="", installer_rcs=[1, 0])
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "rolled_back")
        self.assertEqual(runner.checkouts, [LATEST, CURRENT])

    def test_rollback_uses_the_sha_when_the_branch_has_moved(self):
        # The branch no longer points at the pre-update commit, so restoring it
        # would roll back to the wrong revision. Prefer the recorded sha.
        runner = FakeRunner(head=CURRENT, branch="main", branch_sha=LATEST,
                            installer_rcs=[1, 0])
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "rolled_back")
        self.assertEqual(runner.checkouts, [LATEST, CURRENT])

    def test_rollback_error_carries_the_installers_own_reason(self):
        # Regression: the error was just "exit 1", so every rollback looked
        # identical and no failure could ever be diagnosed from the result.
        runner = FakeRunner(
            head=CURRENT, installer_rcs=[1, 0],
            installer_stderr="!! Homebrew is not installed and could not be "
                             "installed without a terminal")
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "rolled_back")
        self.assertIn("exit 1", outcome["error"])
        self.assertIn("Homebrew is not installed", outcome["error"])

    def test_installer_error_detail_stays_bounded(self):
        runner = FakeRunner(head=CURRENT, installer_rcs=[1, 0],
                            installer_stderr="x" * 5000)
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertLess(len(outcome["error"]), 800)

    def test_checkout_failure_stays_on_previous(self):
        # git refuses the checkout: nothing moved, installer must never run.
        runner = FakeRunner(head=CURRENT, checkout_rc=1)
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["from"], CURRENT)
        self.assertIsNotNone(outcome["error"])
        self.assertEqual(runner.checkouts, [LATEST])
        self.assertEqual(runner.installer_runs, 0)

    def test_missing_repo_returns_clean_failure(self):
        runner = FakeRunner(head="")
        outcome = self_update.apply_update(CHECKOUT, LATEST, runner=runner, install_receipt=NO_RECEIPT)
        self.assertEqual(outcome["status"], "failed")
        self.assertEqual(outcome["error"], "not a git checkout")
        self.assertEqual(runner.checkouts, [])
        self.assertEqual(runner.installer_runs, 0)


if __name__ == "__main__":
    unittest.main()
