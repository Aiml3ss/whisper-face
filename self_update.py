"""Git-based, opt-in self-update for the Whisper Face checkout.

This is the *unsigned local-checkout* update path. The app runs directly from a
git working copy under launchd, so "update" here means fast-forwarding that
checkout to its tracked upstream and re-running the installer. It is deliberately
narrow and fail-closed:

  * user-initiated only -- no background polling, no auto-updates;
  * the only network access is one explicit ``git fetch`` inside
    :func:`check_for_update`;
  * any git/network problem, a dirty working tree, or an unverifiable upstream
    yields ``available=False`` with an ``error`` string -- never a claimed
    update it cannot prove;
  * :func:`apply_update` records the pre-update HEAD and, if the installer
    fails on the new revision, restores that HEAD and re-installs, so the
    checkout is never left on a broken revision.

Everything is driven through an injected ``runner`` (a ``subprocess.run``-style
callable) and an explicit ``checkout`` path, so the logic is unit-testable
offline with a fake runner and never hard-codes a network endpoint.

The separate, future *signed-release* path (``scripts/release_manifest.py``,
``scripts/safe_update_advisor.py``, ``scripts/side_by_side_update.py``) is for
published, notarized releases and is intentionally untouched by this module.
"""

from __future__ import annotations

import argparse
import json
import os
import subprocess
from pathlib import Path
from typing import Callable

DEFAULT_REMOTE = "origin"
DEFAULT_INSTALLER = "Install.command"

# The installer's own receipt. Its ``source_revision`` is the revision that was
# last actually provisioned -- which is the app the user is running, and is not
# the same thing as the checkout's HEAD once anything moves the checkout without
# going through the installer.
DEFAULT_INSTALL_RECEIPT = (
    Path.home() / "Library" / "Application Support" / "Whisper Face" /
    "launcher-install.json")

# One network op (fetch) plus fast local plumbing; keep a menu-driven,
# background-thread check bounded so it can never hang the app forever.
_FETCH_TIMEOUT = 60.0
_GIT_TIMEOUT = 15.0
_INSTALL_TIMEOUT = 1800.0

# Enough of a failing installer's tail to name the cause, small enough to sit
# in a result file and an alert without becoming a wall of text.
_ERROR_DETAIL_LIMIT = 600

# Runner is any subprocess.run-style callable returning a CompletedProcess.
Runner = Callable[..., "subprocess.CompletedProcess"]


def _describe(exc: BaseException) -> str:
    """A short, privacy-preserving reason for a git failure (no paths/URLs)."""
    if isinstance(exc, FileNotFoundError):
        return "git is not installed"
    if isinstance(exc, subprocess.TimeoutExpired):
        return "git operation timed out"
    return f"git error ({type(exc).__name__})"


def _git(runner: Runner, checkout: Path, *args: str,
         timeout: float = _GIT_TIMEOUT) -> "subprocess.CompletedProcess":
    """Run one ``git -C <checkout> ...`` command through the injected runner.

    Uses ``-C`` (not ``cwd``) so the command is fully described by its argv,
    which keeps both the real call and the test fake trivial. Never raises for a
    non-zero git exit; callers inspect ``returncode`` and fail closed.
    """
    return runner(
        ["git", "-C", str(checkout), *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def _rev_parse(runner: Runner, checkout: Path, ref: str,
               timeout: float = _GIT_TIMEOUT):
    """Resolve ``ref`` to a commit sha, or ``None`` if it does not resolve."""
    result = _git(runner, checkout, "rev-parse", "--verify", "--quiet", ref,
                  timeout=timeout)
    if result.returncode == 0:
        sha = (result.stdout or "").strip()
        return sha or None
    return None


def _installed_revision(receipt) -> str:
    """Read ``source_revision`` from the installer's receipt, or ``""``.

    Absent, unreadable, or malformed all mean the same thing here -- no usable
    record -- and the caller falls back to HEAD, which is exactly how this
    module behaved before the receipt existed.
    """
    try:
        payload = json.loads(Path(receipt).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return ""
    revision = payload.get("source_revision") if isinstance(payload, dict) else None
    return revision if isinstance(revision, str) and revision.strip() else ""


def _current_branch(runner: Runner, checkout: Path) -> str:
    """Return the checked-out branch name, or ``""`` when HEAD is detached."""
    result = _git(runner, checkout, "symbolic-ref", "--short", "--quiet",
                  "HEAD")
    if result.returncode == 0:
        return (result.stdout or "").strip()
    return ""


def _restore(runner: Runner, checkout: Path, previous: str,
             branch: str) -> None:
    """Put the checkout back on ``previous``, preferring its original branch.

    An update moves HEAD alone, so the branch it started on still points at
    ``previous``. Checking that branch out again rolls back to the same commit
    *and* leaves the checkout attached; restoring the bare sha instead used to
    strand the user on a detached HEAD, where ``git pull`` no longer works and
    upstream tracking is gone. Falls back to the sha when HEAD was already
    detached or the branch has since moved.
    """
    if branch and _rev_parse(runner, checkout, branch) == previous:
        if _git(runner, checkout, "checkout", "--quiet",
                branch).returncode == 0:
            return
    _git(runner, checkout, "checkout", "--quiet", previous)


def check_for_update(checkout, remote: str = DEFAULT_REMOTE, *,
                     runner: Runner, install_receipt=None) -> dict:
    """Report whether the tracked upstream is ahead of the *installed* revision.

    Returns ``{"available", "current", "latest", "behind", "error"}``. Fails
    closed: any git/network failure, a non-repo, or a dirty working tree returns
    ``available=False`` with a human-readable ``error`` and never raises. The
    only network access is the single ``git fetch``.

    ``current`` is the revision the installer last provisioned, not the
    checkout's HEAD. The two are identical for anyone whose checkout only ever
    moves through this module, but they diverge the moment something else moves
    it -- a developer pulling, a script, a rolled-back update. HEAD then
    describes files on disk while the user is still running the older build, and
    comparing HEAD to upstream would answer "up to date" to someone who is
    demonstrably not. Comparing the installed revision answers the question the
    user actually asked. Applying the update re-runs the installer, which
    refreshes the receipt, so a stale receipt corrects itself on first use.
    """
    checkout = Path(checkout)
    receipt = DEFAULT_INSTALL_RECEIPT if install_receipt is None else install_receipt
    result = {"available": False, "current": "", "latest": "",
              "behind": 0, "error": None}
    try:
        current = _rev_parse(runner, checkout, "HEAD")
        if not current:
            result["error"] = "not a git checkout"
            return result

        # Prefer what was installed over what is checked out. Resolve it through
        # git so an unknown or garbage revision falls back to HEAD rather than
        # poisoning the comparison.
        installed = _installed_revision(receipt)
        if installed and installed != current:
            resolved = _rev_parse(runner, checkout, f"{installed}^{{commit}}")
            if resolved:
                current = resolved
        result["current"] = current

        # Refuse when the working tree carries uncommitted changes: applying an
        # update would checkout over them. Fail closed before touching the
        # network -- there is nothing a fetch could tell us that would make an
        # update safe here.
        status = _git(runner, checkout, "status", "--porcelain")
        if status.returncode != 0:
            result["error"] = "could not read git status"
            return result
        if (status.stdout or "").strip():
            result["error"] = "local changes present"
            return result

        # The one and only network access in this module.
        fetched = _git(runner, checkout, "fetch", "--quiet", remote,
                       timeout=_FETCH_TIMEOUT)
        if fetched.returncode != 0:
            result["error"] = f"could not fetch from {remote!r}"
            return result

        # Prefer the branch's own tracked upstream; fall back to the remote's
        # default branch, then its main. If none resolve, refuse rather than
        # guess.
        latest = None
        for ref in ("@{u}", f"{remote}/HEAD", f"{remote}/main"):
            latest = _rev_parse(runner, checkout, ref)
            if latest:
                break
        if not latest:
            result["error"] = "no tracked upstream branch"
            return result
        result["latest"] = latest

        if latest == current:
            return result  # up to date: available stays False, behind 0

        counted = _git(runner, checkout, "rev-list", "--count",
                       f"{current}..{latest}")
        if counted.returncode != 0:
            result["error"] = "could not compare with upstream"
            return result
        try:
            behind = int((counted.stdout or "").strip() or "0")
        except ValueError:
            result["error"] = "could not parse commit count"
            return result

        result["behind"] = behind
        result["available"] = behind > 0
        return result
    except (OSError, subprocess.SubprocessError) as exc:
        # Missing git binary, fetch timeout, etc. Never propagate -- fail closed.
        result["available"] = False
        result["error"] = _describe(exc)
        return result


def _run_installer(runner: Runner, checkout: Path,
                   installer: str) -> "subprocess.CompletedProcess":
    """Run the checkout's installer from the checkout directory.

    A missing or non-executable installer is reported as a non-zero result
    rather than an exception, so the single "installer failed -> roll back" path
    in :func:`apply_update` handles every failure mode uniformly.
    """
    command = [f"./{installer}"]
    try:
        return runner(command, cwd=str(checkout), capture_output=True,
                      text=True, timeout=_INSTALL_TIMEOUT)
    except (OSError, subprocess.SubprocessError):
        return subprocess.CompletedProcess(command, 127, "", "")


def _installer_error(result: "subprocess.CompletedProcess",
                     installer: str) -> str:
    """Summarise an installer failure, keeping the tail of its own output.

    An exit code on its own cannot tell a missing dependency from a failed
    download, which left every rollback unexplainable. The installer already
    prints a precise reason before it exits; keep a bounded tail of it so the
    result, the log, and the user-facing alert can all say what went wrong.
    """
    summary = f"{installer} failed (exit {result.returncode})"
    for stream in (result.stderr, result.stdout):
        detail = (stream or "").strip()
        if detail:
            return f"{summary}: {detail[-_ERROR_DETAIL_LIMIT:]}"
    return summary


def apply_update(checkout, target_rev: str, *, runner: Runner,
                 installer: str = DEFAULT_INSTALLER) -> dict:
    """Move the checkout to ``target_rev`` and re-run the installer.

    Records the pre-update HEAD as ``from``. On success returns
    ``{"status": "applied", "from", "to"}``. If the installer fails on the new
    revision, restores the previous HEAD, re-runs the installer, and returns
    ``{"status": "rolled_back", "from", "to", "error"}``. If the checkout cannot
    even be switched (or this is not a repo), nothing moved and it returns
    ``{"status": "failed", ...}``. The checkout is never left on a broken
    revision.
    """
    checkout = Path(checkout)
    result = {"status": "failed", "from": "", "to": target_rev, "error": None}
    # Bound before the try so the recovery path can still name the branch when
    # the failure happens partway through reading it.
    previous_branch = ""
    try:
        previous = _rev_parse(runner, checkout, "HEAD")
        if not previous:
            result["error"] = "not a git checkout"
            return result
        result["from"] = previous
        previous_branch = _current_branch(runner, checkout)

        checked_out = _git(runner, checkout, "checkout", "--quiet", target_rev)
        if checked_out.returncode != 0:
            # Never moved off `previous`; there is nothing to roll back.
            result["error"] = f"could not checkout {target_rev}"
            return result

        installed = _run_installer(runner, checkout, installer)
        if installed.returncode == 0:
            result["status"] = "applied"
            return result

        # Installer failed on the new revision: restore the old one and
        # re-install so the checkout is never left broken.
        forward_error = _installer_error(installed, installer)
        _restore(runner, checkout, previous, previous_branch)
        _run_installer(runner, checkout, installer)
        result["status"] = "rolled_back"
        result["error"] = forward_error
        return result
    except (OSError, subprocess.SubprocessError) as exc:
        # Unexpected failure mid-apply (e.g. a git timeout). Best-effort restore
        # to the recorded previous revision so we do not strand a broken tree.
        previous = result.get("from")
        if previous:
            try:
                _restore(runner, checkout, previous, previous_branch)
                _run_installer(runner, checkout, installer)
                result["status"] = "rolled_back"
            except (OSError, subprocess.SubprocessError):
                result["status"] = "failed"
        result["error"] = _describe(exc)
        return result


def _write_result(path: Path, payload: dict) -> None:
    """Atomically persist a private update result across service restarts."""
    path = path.expanduser().resolve()
    path.parent.mkdir(parents=True, exist_ok=True)
    os.chmod(path.parent, 0o700)
    temporary = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    temporary.write_text(
        json.dumps(payload, sort_keys=True, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )
    os.chmod(temporary, 0o600)
    os.replace(temporary, path)


def apply_update_detached(
        checkout, target_rev: str, result_path, *, label: str = "") -> int:
    """Apply an update from a launchd job independent of the app service.

    ``Install.command`` reloads ``com.berg.dictate``. Running the installer
    inside that service therefore kills an in-process updater. This helper
    persists its state outside the checkout, survives the reload, and performs
    one final restart after the result is durable.
    """
    checkout = Path(checkout).expanduser().resolve()
    result_path = Path(result_path).expanduser().resolve()
    _write_result(result_path, {
        "status": "running",
        "from": _rev_parse(subprocess.run, checkout, "HEAD") or "",
        "to": target_rev,
        "error": None,
    })
    try:
        outcome = apply_update(
            checkout, target_rev, runner=subprocess.run)
    except Exception as exc:
        outcome = {
            "status": "failed",
            "from": "",
            "to": target_rev,
            "error": type(exc).__name__,
        }
    _write_result(result_path, outcome)
    subprocess.run(
        ["/bin/launchctl", "kickstart", "-k",
         f"gui/{os.getuid()}/com.berg.dictate"],
        capture_output=True,
        text=True,
        timeout=_GIT_TIMEOUT,
    )
    if label:
        subprocess.run(
            ["/bin/launchctl", "remove", label],
            capture_output=True,
            text=True,
            timeout=_GIT_TIMEOUT,
        )
    return 0 if outcome.get("status") == "applied" else 1


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    subparsers = parser.add_subparsers(dest="command", required=True)
    detached = subparsers.add_parser("apply-detached")
    detached.add_argument("--checkout", required=True)
    detached.add_argument("--target", required=True)
    detached.add_argument("--result", required=True)
    detached.add_argument("--label", default="")
    args = parser.parse_args(argv)
    return apply_update_detached(
        args.checkout,
        args.target,
        args.result,
        label=args.label,
    )


if __name__ == "__main__":
    raise SystemExit(main())
