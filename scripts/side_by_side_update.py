#!/usr/bin/env python3
"""Apply a verified local Whisper Face candidate without replacing its rollback.

The planner has no network, download, checkout-switch, reset, or copy
operation. An operator prepares a separate sibling checkout first, reviews the
plan, then explicitly asks this tool to run that candidate's own installer.
That installer may download missing prerequisites or models, provision private
state, and rebind services; the applied receipt reports those effects honestly.
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
from pathlib import Path
from types import SimpleNamespace
from typing import Callable, Sequence

import release_manifest
import safe_update_advisor


REVISION = re.compile(r"^[0-9a-f]{40}$")
REQUIRED_CANDIDATE_FILES = (
    "Install.command", "dictate.py", "dictate.py.lock", "setup.sh",
    "scripts/release_manifest.py", "scripts/safe_update_advisor.py",
    "scripts/side_by_side_update.py",
)
NO_EFFECTS = {
    "candidate_setup": False,
    "current_checkout_mutation": False,
    "download": False,
    "launchd_change": False,
    "network": False,
    "private_state": False,
    "source_overwrite": False,
}


class UpdateError(ValueError):
    """The prepared candidate cannot be applied safely."""


Runner = Callable[..., subprocess.CompletedProcess[str]]


def _run(command: Sequence[str], *, cwd: Path, runner: Runner) -> str:
    try:
        result = runner(list(command), cwd=str(cwd), text=True,
                        capture_output=True, check=False)
    except (OSError, subprocess.SubprocessError) as error:
        raise UpdateError("local-command-unavailable") from error
    if result.returncode != 0:
        raise UpdateError("local-checkout-validation-failed")
    return (result.stdout or "").strip()


def _checkout(path: str, *, label: str, runner: Runner) -> tuple[Path, str]:
    checkout = Path(path).expanduser().resolve()
    if not checkout.is_dir():
        raise UpdateError(f"{label}-checkout-missing")
    revision = _run(("git", "rev-parse", "--verify", "HEAD^{commit}"),
                    cwd=checkout, runner=runner).casefold()
    if not REVISION.fullmatch(revision):
        raise UpdateError(f"{label}-revision-invalid")
    if _run(("git", "status", "--porcelain"), cwd=checkout, runner=runner):
        raise UpdateError(f"{label}-checkout-dirty")
    return checkout, revision


def _validate_candidate_files(candidate: Path) -> None:
    for relative in REQUIRED_CANDIDATE_FILES:
        path = candidate / relative
        if not path.is_file() or path.is_symlink():
            raise UpdateError("candidate-required-file-missing")
    if not (candidate / "setup.sh").stat().st_mode & 0o111:
        raise UpdateError("candidate-setup-not-executable")


def _origin(checkout: Path, *, runner: Runner) -> str:
    origin = _run(("git", "config", "--get", "remote.origin.url"),
                  cwd=checkout, runner=runner).rstrip("/")
    if origin.endswith(".git"):
        origin = origin[:-4]
    if not origin:
        raise UpdateError("checkout-origin-missing")
    return origin.casefold()


def _verified_manifest(path: str, artifact_dir: str) -> dict:
    try:
        return release_manifest.verify_manifest(Path(path), Path(artifact_dir))
    except (release_manifest.ManifestError, OSError) as error:
        raise UpdateError("manifest-or-artifact-verification-failed") from error


def _manifest_plan(*, current_version: str, current_revision: str,
                   candidate_revision: str, manifest: str | None,
                   artifact_dir: str | None, current_manifest: str | None,
                   current_artifact_dir: str | None, channel: str) -> dict | None:
    supplied = (manifest, artifact_dir, current_manifest, current_artifact_dir)
    if not any(supplied):
        return None
    if not all(supplied):
        raise UpdateError("current-and-candidate-manifests-must-be-paired")
    current = _verified_manifest(current_manifest, current_artifact_dir)
    if (current.get("channel") != channel
            or current.get("version") != current_version
            or current.get("source_offer", {}).get("revision")
            != current_revision):
        raise UpdateError("current-checkout-does-not-match-manifest")
    candidate_manifest = _verified_manifest(manifest, artifact_dir)
    if candidate_manifest.get("source_offer", {}).get("revision") != candidate_revision:
        raise UpdateError("candidate-checkout-does-not-match-manifest")
    rollback = candidate_manifest.get("rollback")
    if (not isinstance(rollback, dict) or rollback.get("supported") is not True
            or rollback.get("strategy") != "install-previous-release"
            or rollback.get("version") != current_version
            or rollback.get("source_revision") != current_revision):
        raise UpdateError("candidate-manifest-is-not-linked-to-current-release")
    try:
        receipt = safe_update_advisor.advise(SimpleNamespace(
            current_version=current_version, current_revision=current_revision,
            manifest=manifest, artifact_dir=artifact_dir, channel=channel,
            intent="update", rollback_manifest=None,
            rollback_artifact_dir=None))
    except safe_update_advisor.AdviceError as error:
        raise UpdateError(str(error)) from error
    candidate = receipt.get("candidate", {})
    if (receipt.get("decision") != "upgrade"
            or candidate.get("source_revision") != candidate_revision):
        raise UpdateError("candidate-does-not-match-verified-upgrade")
    return receipt


def plan_side_by_side_update(*, current_checkout: str, candidate_checkout: str,
                             current_version: str, manifest: str | None = None,
                             artifact_dir: str | None = None,
                             current_manifest: str | None = None,
                             current_artifact_dir: str | None = None,
                             channel: str = "stable",
                             runner: Runner = subprocess.run) -> dict:
    """Validate a prepared sibling checkout without changing either checkout."""
    current, current_revision = _checkout(current_checkout, label="current",
                                          runner=runner)
    candidate, candidate_revision = _checkout(candidate_checkout,
                                              label="candidate", runner=runner)
    if current == candidate or current.parent != candidate.parent:
        raise UpdateError("candidate-must-be-a-distinct-sibling-checkout")
    if current_revision == candidate_revision:
        raise UpdateError("candidate-reuses-current-revision")
    _validate_candidate_files(candidate)
    if _origin(current, runner=runner) != _origin(candidate, runner=runner):
        raise UpdateError("candidate-origin-does-not-match-current")
    manifest_receipt = _manifest_plan(
        current_version=current_version, current_revision=current_revision,
        candidate_revision=candidate_revision, manifest=manifest,
        artifact_dir=artifact_dir, current_manifest=current_manifest,
        current_artifact_dir=current_artifact_dir, channel=channel)
    return {
        "candidate": {"source_revision": candidate_revision},
        "channel": channel,
        "current": {"source_revision": current_revision},
        "decision": ("apply-side-by-side" if manifest_receipt is not None
                     else "review-local-candidate"),
        "effects": dict(NO_EFFECTS),
        "execution": "none",
        "manifest_verified": manifest_receipt is not None,
        "authority": {
            "candidate_revision": candidate_revision,
            "current_revision": current_revision,
            "current_version": current_version,
            "manifest_linked": manifest_receipt is not None,
        },
        "reason": "prepared-candidate-validated",
        "schema_version": 1,
    }


def apply_side_by_side_update(*, current_checkout: str, candidate_checkout: str,
                              current_version: str, manifest: str | None,
                              artifact_dir: str | None,
                              current_manifest: str | None,
                              current_artifact_dir: str | None,
                              channel: str, reviewed_plan: dict,
                              runner: Runner = subprocess.run) -> dict:
    """Run only the validated candidate installer and its read-only verifier."""
    if not isinstance(reviewed_plan, dict):
        raise UpdateError("reviewed-plan-required")
    plan = plan_side_by_side_update(
        current_checkout=current_checkout, candidate_checkout=candidate_checkout,
        current_version=current_version, manifest=manifest,
        artifact_dir=artifact_dir, current_manifest=current_manifest,
        current_artifact_dir=current_artifact_dir, channel=channel, runner=runner)
    if plan["authority"]["manifest_linked"] is not True:
        raise UpdateError("apply-requires-linked-current-and-candidate-manifests")
    if (reviewed_plan.get("decision") != "apply-side-by-side"
            or reviewed_plan.get("authority") != plan["authority"]):
        raise UpdateError("update-authority-changed-after-review")
    candidate = Path(candidate_checkout).expanduser().resolve()
    _run(("./setup.sh",), cwd=candidate, runner=runner)
    _run(("./setup.sh", "--verify"), cwd=candidate, runner=runner)
    applied = dict(plan)
    applied["effects"] = {**NO_EFFECTS, "candidate_setup": True,
                           "download": "possible", "launchd_change": True,
                           "network": "possible", "private_state": "possible"}
    applied["execution"] = "candidate-setup-and-verify"
    applied["reason"] = "candidate-installed-and-verified"
    return applied


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--current-checkout", required=True)
    parser.add_argument("--candidate-checkout", required=True)
    parser.add_argument("--current-version", required=True)
    parser.add_argument("--manifest")
    parser.add_argument("--artifact-dir")
    parser.add_argument("--current-manifest")
    parser.add_argument("--current-artifact-dir")
    parser.add_argument("--channel", choices=("stable", "preview"),
                        default="stable")
    parser.add_argument("--apply", action="store_true",
                        help="run the candidate setup and --verify after planning")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        plan = plan_side_by_side_update(
            current_checkout=args.current_checkout,
            candidate_checkout=args.candidate_checkout,
            current_version=args.current_version, manifest=args.manifest,
            artifact_dir=args.artifact_dir, current_manifest=args.current_manifest,
            current_artifact_dir=args.current_artifact_dir, channel=args.channel)
        receipt = (apply_side_by_side_update(
            current_checkout=args.current_checkout,
            candidate_checkout=args.candidate_checkout,
            current_version=args.current_version, manifest=args.manifest,
            artifact_dir=args.artifact_dir, current_manifest=args.current_manifest,
            current_artifact_dir=args.current_artifact_dir, channel=args.channel,
            reviewed_plan=plan)
            if args.apply else plan)
    except UpdateError as error:
        receipt = {"decision": "refuse", "effects": dict(NO_EFFECTS),
                   "execution": "none", "reason": str(error),
                   "schema_version": 1}
        print(json.dumps(receipt, sort_keys=True))
        return 2
    print(json.dumps(receipt, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
