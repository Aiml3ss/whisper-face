"""Private entry point for the experimental macOS networkless worker.

This process is not a public SDK, ABI, or XPC service.  It serves exactly one
bounded Voice Input Protocol exchange and exits.  It must be launched through
``macos_networkless_worker``; direct runtime activation is intentionally absent.
"""

from __future__ import annotations

import errno
import socket
import sys

from voice_input_protocol import MessageKind, ProtocolMessage
from voice_input_protocol_transport import (
    MAX_DEADLINE_SECONDS,
    UnixProtocolServer,
)


_NETWORK_DENIED_ERRNOS = frozenset({errno.EACCES, errno.EPERM})


def _ip_network_is_denied() -> bool:
    """Prove the active OS policy denies loopback IP bind and connect."""
    operations = (
        lambda probe: probe.bind(("127.0.0.1", 0)),
        lambda probe: probe.connect(("127.0.0.1", 9)),
    )
    for operation in operations:
        probe: socket.socket | None = None
        try:
            probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
            operation(probe)
        except OSError as error:
            if error.errno not in _NETWORK_DENIED_ERRNOS:
                return False
        else:
            return False
        finally:
            if probe is not None:
                probe.close()
    return True


def _content_free_failure(request: ProtocolMessage) -> ProtocolMessage:
    if (request.kind is not MessageKind.CAPTURE_PROPOSAL
            or request.sequence != 0):
        raise ValueError("worker request was rejected")
    return ProtocolMessage(
        utterance_id=request.utterance_id,
        sequence=1,
        kind=MessageKind.CANCELLATION,
        payload={"reason": "capture_failed"},
    )


def _main(arguments: list[str]) -> int:
    if len(arguments) != 2:
        return 64
    socket_path, timeout_text = arguments
    try:
        timeout = float(timeout_text)
    except ValueError:
        return 64
    if not 0 < timeout <= MAX_DEADLINE_SECONDS:
        return 64
    if not _ip_network_is_denied():
        return 77

    server: UnixProtocolServer | None = None
    try:
        server = UnixProtocolServer(socket_path, _content_free_failure)
        server.start()
        return 0 if server.serve_once(timeout=timeout) else 1
    except Exception:
        # Do not print request data, paths, exception text, or tracebacks.
        return 1
    finally:
        if server is not None:
            server.close()


if __name__ == "__main__":
    raise SystemExit(_main(sys.argv[1:]))
