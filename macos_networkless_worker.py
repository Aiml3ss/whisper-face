"""Mac-first, network-denied local Voice Input Protocol worker foundation.

The worker is one-shot, opt-in, and not wired to the Whisper Face runtime.  It
uses Apple's ``sandbox-exec`` primitive and the existing private Unix transport;
it is not XPC and does not define a public SDK or ABI.
"""

from __future__ import annotations

import os
import stat
import subprocess
import sys
import tempfile
import time
from pathlib import Path

from voice_input_protocol import MessageKind, ProtocolMessage
from voice_input_protocol_transport import (
    DEFAULT_DEADLINE_SECONDS,
    MAX_DEADLINE_SECONDS,
    request,
)


SANDBOX_EXEC = Path("/usr/bin/sandbox-exec")
SANDBOX_PROFILE = (
    Path(__file__).resolve().parent / "sandbox" / "macos-voice-input-worker.sb"
)
WORKER_PROCESS = (
    Path(__file__).resolve().parent / "macos_networkless_worker_process.py"
)
_STARTUP_POLL_SECONDS = 0.005


class NetworklessWorkerError(RuntimeError):
    """Base class for content-free worker failures."""


class NetworklessWorkerUnavailable(NetworklessWorkerError):
    """The required macOS sandbox primitive is unavailable."""


class NetworklessWorkerRejected(NetworklessWorkerError):
    """The worker rejected or could not complete the bounded exchange."""


def _timeout(value: float) -> float:
    if (isinstance(value, bool) or not isinstance(value, (int, float))
            or not 0 < float(value) <= MAX_DEADLINE_SECONDS):
        raise ValueError(
            f"deadline must be within 0 and {MAX_DEADLINE_SECONDS} seconds")
    return float(value)


def sandbox_primitive_available(*, timeout: float = 0.5) -> bool:
    """Return whether this host can execute a minimal Apple sandbox profile."""
    timeout = _timeout(timeout)
    if (sys.platform != "darwin" or not SANDBOX_EXEC.is_file()
            or not os.access(SANDBOX_EXEC, os.X_OK)):
        return False
    try:
        completed = subprocess.run(
            [
                os.fspath(SANDBOX_EXEC),
                "-p", "(version 1) (allow default)",
                "/usr/bin/true",
            ],
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=timeout,
            check=False,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return completed.returncode == 0


class MacOSNetworklessWorker:
    """Own one sandboxed worker process and one private request/response."""

    def __init__(self, *, timeout: float = DEFAULT_DEADLINE_SECONDS) -> None:
        self.timeout = _timeout(timeout)
        self._temporary: tempfile.TemporaryDirectory[str] | None = None
        self._process: subprocess.Popen[bytes] | None = None
        self._socket_path: Path | None = None
        self._exchanged = False

    @property
    def socket_path(self) -> Path:
        if self._socket_path is None:
            raise NetworklessWorkerUnavailable("networkless worker is unavailable")
        return self._socket_path

    def start(self) -> None:
        if self._process is not None:
            return
        if self._exchanged:
            raise NetworklessWorkerRejected("networkless worker request rejected")
        if (sys.platform != "darwin" or not SANDBOX_EXEC.is_file()
                or not os.access(SANDBOX_EXEC, os.X_OK)
                or not SANDBOX_PROFILE.is_file()
                or not WORKER_PROCESS.is_file()):
            raise NetworklessWorkerUnavailable(
                "networkless worker is unavailable")

        temporary: tempfile.TemporaryDirectory[str] | None = None
        try:
            temporary = tempfile.TemporaryDirectory(
                prefix="whisper-face-worker-", dir="/tmp")
            # sandbox-exec matches Unix-socket rules against the canonical
            # /private/tmp path rather than macOS's /tmp symlink spelling.
            directory = Path(temporary.name).resolve()
            os.chmod(directory, 0o700)
        except OSError:
            if temporary is not None:
                try:
                    temporary.cleanup()
                except OSError:
                    pass
            raise NetworklessWorkerUnavailable(
                "networkless worker is unavailable") from None

        deadline = time.monotonic() + self.timeout
        socket_path = directory / "protocol.sock"
        python_executable = Path(sys.executable).resolve()
        command = [
            os.fspath(SANDBOX_EXEC),
            "-D", f"IPC_ROOT={directory}",
            "-D", f"PYTHON_EXECUTABLE={python_executable}",
            "-f", os.fspath(SANDBOX_PROFILE),
            os.fspath(python_executable),
            "-B",
            os.fspath(WORKER_PROCESS),
            os.fspath(socket_path),
            str(self.timeout),
        ]
        try:
            process = subprocess.Popen(
                command,
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.DEVNULL,
                close_fds=True,
                env={
                    "PATH": "/usr/bin:/bin",
                    "PYTHONDONTWRITEBYTECODE": "1",
                },
            )
        except OSError:
            try:
                temporary.cleanup()
            except OSError:
                pass
            raise NetworklessWorkerUnavailable(
                "networkless worker is unavailable") from None

        self._temporary = temporary
        self._process = process
        self._socket_path = socket_path
        try:
            while not socket_path.exists():
                if process.poll() is not None:
                    raise NetworklessWorkerUnavailable(
                        "networkless worker is unavailable")
                if time.monotonic() >= deadline:
                    raise NetworklessWorkerUnavailable(
                        "networkless worker is unavailable")
                time.sleep(_STARTUP_POLL_SECONDS)
            self._verify_private_endpoint(directory, socket_path)
        except Exception:
            self.close()
            raise NetworklessWorkerUnavailable(
                "networkless worker is unavailable") from None

    def exchange(self, message: ProtocolMessage) -> ProtocolMessage:
        """Send one content-free request class and receive a terminal receipt."""
        if (not isinstance(message, ProtocolMessage)
                or message.kind is not MessageKind.CAPTURE_PROPOSAL
                or message.sequence != 0
                or self._exchanged):
            raise NetworklessWorkerRejected("networkless worker request rejected")
        self.start()
        self._exchanged = True
        try:
            response = request(self.socket_path, message, timeout=self.timeout)
            process = self._process
            if process is None:
                raise NetworklessWorkerRejected(
                    "networkless worker request rejected")
            process.wait(timeout=self.timeout)
            if (process.returncode != 0
                    or response.utterance_id != message.utterance_id
                    or response.sequence != 1
                    or response.kind is not MessageKind.CANCELLATION
                    or dict(response.payload) != {"reason": "capture_failed"}):
                raise NetworklessWorkerRejected(
                    "networkless worker request rejected")
            return response
        except Exception:
            raise NetworklessWorkerRejected(
                "networkless worker request rejected") from None
        finally:
            self.close()

    def close(self) -> None:
        process, self._process = self._process, None
        try:
            running = process is not None and process.poll() is None
        except OSError:
            running = False
        if process is not None and running:
            try:
                process.terminate()
                process.wait(timeout=min(self.timeout, 0.5))
            except (OSError, subprocess.TimeoutExpired):
                try:
                    process.kill()
                    process.wait(timeout=0.5)
                except (OSError, subprocess.TimeoutExpired):
                    pass
        temporary, self._temporary = self._temporary, None
        self._socket_path = None
        if temporary is not None:
            try:
                temporary.cleanup()
            except OSError:
                pass

    @staticmethod
    def _verify_private_endpoint(directory: Path, socket_path: Path) -> None:
        directory_stat = directory.stat()
        socket_stat = socket_path.stat()
        expected_uid = os.geteuid()
        if (directory_stat.st_uid != expected_uid
                or socket_stat.st_uid != expected_uid
                or stat.S_IMODE(directory_stat.st_mode) != 0o700
                or stat.S_IMODE(socket_stat.st_mode) != 0o600
                or not stat.S_ISSOCK(socket_stat.st_mode)):
            raise NetworklessWorkerUnavailable(
                "networkless worker is unavailable")

    def __enter__(self) -> "MacOSNetworklessWorker":
        self.start()
        return self

    def __exit__(self, *_details: object) -> None:
        self.close()


def exchange_once(
    message: ProtocolMessage,
    *,
    timeout: float = DEFAULT_DEADLINE_SECONDS,
) -> ProtocolMessage:
    """Run one opt-in sandboxed exchange without activating a service."""
    with MacOSNetworklessWorker(timeout=timeout) as worker:
        return worker.exchange(message)


__all__ = [
    "MacOSNetworklessWorker",
    "NetworklessWorkerError",
    "NetworklessWorkerRejected",
    "NetworklessWorkerUnavailable",
    "SANDBOX_EXEC",
    "SANDBOX_PROFILE",
    "exchange_once",
    "sandbox_primitive_available",
]
