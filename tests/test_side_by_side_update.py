# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import importlib.util
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT / "scripts") not in sys.path:
    sys.path.insert(0, str(ROOT / "scripts"))
SPEC = importlib.util.spec_from_file_location(
    "side_by_side_update", ROOT / "scripts" / "side_by_side_update.py")
MODULE = importlib.util.module_from_spec(SPEC)
assert SPEC.loader is not None
SPEC.loader.exec_module(MODULE)
CURRENT, CANDIDATE = "1" * 40, "2" * 40


class SideBySideUpdateTests(unittest.TestCase):
    def make_checkout(self, parent: Path, name: str) -> Path:
        checkout = parent / name
        for relative in MODULE.REQUIRED_CANDIDATE_FILES:
            path = checkout / relative
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text("#!/bin/sh\n", encoding="utf-8")
        (checkout / "setup.sh").chmod(0o755)
        return checkout.resolve()

    @staticmethod
    def runner(revisions, statuses, calls, origins=None):
        origins = origins or {checkout: "https://example.invalid/whisper-face"
                              for checkout in revisions}
        def run(command, *, cwd, text, capture_output, check):
            checkout = Path(cwd).resolve()
            calls.append((tuple(command), checkout))
            if command[:3] == ["git", "rev-parse", "--verify"]:
                return subprocess.CompletedProcess(command, 0,
                                                   revisions[checkout] + "\n", "")
            if command == ["git", "status", "--porcelain"]:
                return subprocess.CompletedProcess(command, 0,
                                                   statuses[checkout], "")
            if command == ["git", "config", "--get", "remote.origin.url"]:
                return subprocess.CompletedProcess(command, 0, origins[checkout], "")
            if command in (["./setup.sh"], ["./setup.sh", "--verify"]):
                return subprocess.CompletedProcess(command, 0, "", "")
            raise AssertionError(command)
        return run

    def test_plan_is_read_only_and_keeps_current_checkout_intact(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            current, candidate = (self.make_checkout(parent, name)
                                  for name in ("current", "candidate;never-run"))
            calls = []
            plan = MODULE.plan_side_by_side_update(
                current_checkout=str(current), candidate_checkout=str(candidate),
                current_version="1.0.0", channel="preview", runner=self.runner(
                    {current: CURRENT, candidate: CANDIDATE},
                    {current: "", candidate: ""}, calls))
        self.assertEqual(plan["decision"], "review-local-candidate")
        self.assertEqual(plan["execution"], "none")
        self.assertFalse(any(plan["effects"].values()))
        self.assertEqual(plan["current"]["source_revision"], CURRENT)
        self.assertFalse(any(command[0][0] == "./setup.sh" for command in calls))
        self.assertTrue(all(isinstance(command[0], tuple) for command in calls))

    def test_apply_requires_linked_manifests_then_runs_candidate_only(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            current, candidate = (self.make_checkout(parent, name)
                                  for name in ("current", "candidate"))
            calls = []
            runner = self.runner({current: CURRENT, candidate: CANDIDATE},
                                 {current: "", candidate: ""}, calls)
            common = dict(
                current_checkout=str(current), candidate_checkout=str(candidate),
                current_version="1.0.0", channel="preview", runner=runner)
            with self.assertRaisesRegex(MODULE.UpdateError, "apply-requires"):
                MODULE.apply_side_by_side_update(
                    manifest=None, artifact_dir=None, current_manifest=None,
                    current_artifact_dir=None, reviewed_plan={}, **common)
            current_payload = {"channel": "preview", "version": "1.0.0", "source_offer": {
                "revision": CURRENT}}
            candidate_payload = {"channel": "preview", "version": "1.1.0", "source_offer": {
                "revision": CANDIDATE}, "rollback": {"supported": True,
                "strategy": "install-previous-release", "version": "1.0.0",
                "source_revision": CURRENT}}
            advisor = {"decision": "upgrade", "candidate": {
                "source_revision": CANDIDATE}}
            with patch.object(MODULE, "_verified_manifest",
                              side_effect=[current_payload, candidate_payload] * 2), \
                    patch.object(MODULE.safe_update_advisor, "advise",
                                 return_value=advisor):
                receipt = MODULE.apply_side_by_side_update(
                    manifest="candidate.json", artifact_dir="candidate-artifacts",
                    current_manifest="current.json",
                    current_artifact_dir="current-artifacts", reviewed_plan={
                        "decision": "apply-side-by-side", "authority": {
                            "candidate_revision": CANDIDATE,
                            "current_revision": CURRENT,
                            "current_version": "1.0.0",
                            "manifest_linked": True}}, **common)
        self.assertEqual(receipt["execution"], "candidate-setup-and-verify")
        self.assertTrue(receipt["effects"]["candidate_setup"])
        self.assertEqual(receipt["effects"]["download"], "possible")
        self.assertEqual(receipt["effects"]["private_state"], "possible")
        self.assertEqual([item for item in calls if item[0][0] == "./setup.sh"], [
            (("./setup.sh",), candidate),
            (("./setup.sh", "--verify"), candidate),
        ])

    def test_dirty_same_changed_or_incomplete_candidate_refuses(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            current = self.make_checkout(parent, "current")
            candidate = self.make_checkout(parent, "candidate")
            dirty = self.runner({current: CURRENT, candidate: CANDIDATE},
                                {current: "", candidate: " M dictate.py\n"}, [])
            with self.assertRaisesRegex(MODULE.UpdateError, "candidate-checkout-dirty"):
                MODULE.plan_side_by_side_update(
                    current_checkout=str(current), candidate_checkout=str(candidate),
                    current_version="1.0.0", channel="preview", runner=dirty)
            clean = self.runner({current: CURRENT, candidate: CANDIDATE},
                                {current: "", candidate: ""}, [])
            with self.assertRaisesRegex(MODULE.UpdateError, "distinct-sibling"):
                MODULE.plan_side_by_side_update(
                    current_checkout=str(current), candidate_checkout=str(current),
                    current_version="1.0.0", channel="preview", runner=clean)
            (candidate / "dictate.py").unlink()
            with self.assertRaisesRegex(MODULE.UpdateError, "required-file"):
                MODULE.plan_side_by_side_update(
                    current_checkout=str(current), candidate_checkout=str(candidate),
                    current_version="1.0.0", channel="preview", runner=clean)

    def test_apply_refuses_candidate_revision_drift(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            current, candidate = (self.make_checkout(parent, name)
                                  for name in ("current", "candidate"))
            clean = self.runner({current: CURRENT, candidate: CANDIDATE},
                                {current: "", candidate: ""}, [])
            plan = MODULE.plan_side_by_side_update(
                current_checkout=str(current), candidate_checkout=str(candidate),
                current_version="1.0.0", channel="preview", runner=clean)
            changed = self.runner({current: CURRENT, candidate: "3" * 40},
                                  {current: "", candidate: ""}, [])
            current_payload = {"channel": "preview", "version": "1.0.0", "source_offer": {
                "revision": CURRENT}}
            candidate_payload = {"channel": "preview", "version": "1.1.0", "source_offer": {
                "revision": CANDIDATE}, "rollback": {"supported": True,
                "strategy": "install-previous-release", "version": "1.0.0",
                "source_revision": CURRENT}}
            with patch.object(MODULE, "_verified_manifest",
                              side_effect=[current_payload, candidate_payload]), \
                    patch.object(MODULE.safe_update_advisor, "advise",
                                 return_value={"decision": "upgrade", "candidate": {
                                     "source_revision": CANDIDATE}}), \
                    self.assertRaisesRegex(MODULE.UpdateError,
                                           "candidate-checkout-does-not-match-manifest"):
                MODULE.apply_side_by_side_update(
                    current_checkout=str(current), candidate_checkout=str(candidate),
                    current_version="1.0.0", manifest="candidate.json",
                    artifact_dir="candidate-artifacts", current_manifest="current.json",
                    current_artifact_dir="current-artifacts", channel="preview",
                    reviewed_plan=plan, runner=changed)

    def test_optional_manifest_must_describe_this_candidate_upgrade(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            current, candidate = (self.make_checkout(parent, name)
                                  for name in ("current", "candidate"))
            runner = self.runner({current: CURRENT, candidate: CANDIDATE},
                                 {current: "", candidate: ""}, [])
            current_payload = {"channel": "preview", "version": "1.0.0", "source_offer": {
                "revision": CURRENT}}
            candidate_payload = {"channel": "preview", "version": "1.1.0", "source_offer": {
                "revision": CANDIDATE}, "rollback": {"supported": True,
                "strategy": "install-previous-release", "version": "1.0.0",
                "source_revision": CURRENT}}
            receipt = {"decision": "upgrade", "candidate": {
                "source_revision": CANDIDATE}}
            with patch.object(MODULE, "_verified_manifest",
                              side_effect=[current_payload, candidate_payload]), \
                    patch.object(MODULE.safe_update_advisor, "advise",
                                 return_value=receipt):
                plan = MODULE.plan_side_by_side_update(
                    current_checkout=str(current), candidate_checkout=str(candidate),
                    current_version="1.0.0", manifest="local.json",
                    artifact_dir="artifacts", current_manifest="current.json",
                    current_artifact_dir="current-artifacts", channel="preview",
                    runner=runner)
            self.assertTrue(plan["manifest_verified"])

    def test_origin_and_manifest_version_linkage_refuse_authority_spoofing(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            current, candidate = (self.make_checkout(parent, name)
                                  for name in ("current", "candidate"))
            different_origins = self.runner(
                {current: CURRENT, candidate: CANDIDATE},
                {current: "", candidate: ""}, [], {
                    current: "https://example.invalid/current",
                    candidate: "https://example.invalid/candidate"})
            with self.assertRaisesRegex(MODULE.UpdateError, "origin-does-not-match"):
                MODULE.plan_side_by_side_update(
                    current_checkout=str(current), candidate_checkout=str(candidate),
                    current_version="1.0.0", channel="preview",
                    runner=different_origins)

            runner = self.runner({current: CURRENT, candidate: CANDIDATE},
                                 {current: "", candidate: ""}, [])
            current_payload = {"channel": "preview", "version": "9.9.9", "source_offer": {
                "revision": CURRENT}}
            candidate_payload = {"channel": "preview", "version": "10.0.0", "source_offer": {
                "revision": CANDIDATE}, "rollback": {"supported": True,
                "strategy": "install-previous-release", "version": "9.9.9",
                "source_revision": CURRENT}}
            with patch.object(MODULE, "_verified_manifest",
                              side_effect=[current_payload, candidate_payload]), \
                    self.assertRaisesRegex(MODULE.UpdateError,
                                           "current-checkout-does-not-match-manifest"):
                MODULE.plan_side_by_side_update(
                    current_checkout=str(current), candidate_checkout=str(candidate),
                    current_version="1.0.0", manifest="candidate.json",
                    artifact_dir="candidate-artifacts", current_manifest="current.json",
                    current_artifact_dir="current-artifacts", channel="preview",
                    runner=runner)


if __name__ == "__main__":
    unittest.main()
