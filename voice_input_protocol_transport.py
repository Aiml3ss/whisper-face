"""Bounded local Unix-socket transport for Voice Input Protocol v1.

This module carries one canonical protocol message request and one canonical
protocol message response per connection.  It is POSIX-only and local to a
single user account; it neither starts a background service nor provides a
network, XPC, sandbox, or Windows transport.
"""

from __future__ import annotations

import os
import socket
import stat
import struct
import time
from collections.abc import Callable
from pathlib import Path

from voice_input_protocol import ProtocolMessage
from voice_input_protocol_wire import MAX_FRAME_BYTES, decode_message, encode_message


MAX_SOCKET_PATH_CHARS = 100
MAX_DEADLINE_SECONDS = 5.0
DEFAULT_DEADLINE_SECONDS = 1.0
_FRAME_PREFIX_BYTES = 4


class ProtocolTransportError(RuntimeError):
    """Base class for content-free local transport failures."""


class ProtocolTransportUnavailable(ProtocolTransportError):
    """Unix-domain sockets are not available on this platform."""


class ProtocolTransportTimeout(ProtocolTransportError):
    """The bounded local request or response did not arrive in time."""


class ProtocolTransportClosed(ProtocolTransportError):
    """The peer closed the connection before a complete response arrived."""


class ProtocolTransportPeerRejected(ProtocolTransportError):
    """A supported peer credential check found a different local user."""


def _require_posix_unix_socket() -> None:
    if os.name == "nt" or not hasattr(socket, "AF_UNIX"):
        raise ProtocolTransportUnavailable(
            "local Unix-domain transport is unavailable on this platform")


def _socket_path(value: str | os.PathLike[str]) -> Path:
    try:
        path = Path(value)
    except TypeError as error:
        raise ValueError("socket path must be path-like") from error
    encoded = os.fsencode(path)
    if (not path.is_absolute() or len(encoded) == 0
            or len(encoded) > MAX_SOCKET_PATH_CHARS):
        raise ValueError("socket path must be absolute and bounded")
    return path


def _deadline(timeout: float) -> float:
    if (isinstance(timeout, bool) or not isinstance(timeout, (int, float))
            or not 0 < float(timeout) <= MAX_DEADLINE_SECONDS):
        raise ValueError(
            f"deadline must be within 0 and {MAX_DEADLINE_SECONDS} seconds")
    return time.monotonic() + float(timeout)


def _remaining(deadline: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise ProtocolTransportTimeout("local protocol deadline expired")
    return remaining


def _set_deadline(sock: socket.socket, deadline: float) -> None:
    sock.settimeout(_remaining(deadline))


def _peer_uid(sock: socket.socket) -> int | None:
    """Read a Unix peer UID where Python and the kernel expose one."""
    getpeereid = getattr(sock, "getpeereid", None)
    if callable(getpeereid):
        uid, _gid = getpeereid()
        return int(uid)
    peercred = getattr(socket, "SO_PEERCRED", None)
    if peercred is not None:
        raw = sock.getsockopt(
            socket.SOL_SOCKET, peercred, struct.calcsize("3i"))
        _pid, uid, _gid = struct.unpack("3i", raw)
        return int(uid)
    return None


def _verify_peer(sock: socket.socket, expected_uid: int,
                 peer_uid_reader: Callable[[socket.socket], int | None]) -> None:
    try:
        uid = peer_uid_reader(sock)
    except (OSError, struct.error, TypeError, ValueError) as error:
        raise ProtocolTransportPeerRejected(
            "local protocol peer credentials were unavailable") from error
    if uid is not None and uid != expected_uid:
        raise ProtocolTransportPeerRejected("local protocol peer was rejected")


def _recv_exact(sock: socket.socket, count: int, deadline: float) -> bytes:
    chunks: list[bytes] = []
    remaining = count
    while remaining:
        _set_deadline(sock, deadline)
        try:
            chunk = sock.recv(remaining)
        except socket.timeout as error:
            raise ProtocolTransportTimeout(
                "local protocol receive timed out") from error
        except OSError as error:
            raise ProtocolTransportClosed(
                "local protocol peer closed") from error
        if not chunk:
            raise ProtocolTransportClosed("local protocol peer closed")
        chunks.append(chunk)
        remaining -= len(chunk)
    return b"".join(chunks)


def _receive_message(sock: socket.socket, deadline: float) -> ProtocolMessage:
    prefix = _recv_exact(sock, _FRAME_PREFIX_BYTES, deadline)
    frame_size = struct.unpack("!I", prefix)[0]
    if frame_size == 0 or frame_size > MAX_FRAME_BYTES:
        raise ProtocolTransportClosed("local protocol frame was rejected")
    return decode_message(_recv_exact(sock, frame_size, deadline))


def _send_message(sock: socket.socket, message: ProtocolMessage,
                  deadline: float) -> None:
    frame = encode_message(message)
    if not frame or len(frame) > MAX_FRAME_BYTES:
        raise ProtocolTransportClosed("local protocol frame was rejected")
    packet = struct.pack("!I", len(frame)) + frame
    _set_deadline(sock, deadline)
    try:
        sock.sendall(packet)
    except socket.timeout as error:
        raise ProtocolTransportTimeout(
            "local protocol send timed out") from error
    except OSError as error:
        raise ProtocolTransportClosed(
            "local protocol peer closed") from error


class UnixProtocolServer:
    """One-at-a-time local request/response server with explicit shutdown."""

    def __init__(
        self,
        path: str | os.PathLike[str],
        handler: Callable[[ProtocolMessage], ProtocolMessage],
        *,
        expected_uid: int | None = None,
        peer_uid_reader: Callable[[socket.socket], int | None] = _peer_uid,
    ) -> None:
        _require_posix_unix_socket()
        if not callable(handler) or not callable(peer_uid_reader):
            raise TypeError("handler and peer_uid_reader must be callable")
        self.path = _socket_path(path)
        self.handler = handler
        self.expected_uid = os.geteuid() if expected_uid is None else expected_uid
        if (isinstance(self.expected_uid, bool)
                or not isinstance(self.expected_uid, int)
                or self.expected_uid < 0):
            raise ValueError("expected_uid must be a non-negative integer")
        self._peer_uid_reader = peer_uid_reader
        self._listener: socket.socket | None = None
        self._owned_path_identity: tuple[int, int] | None = None

    def start(self) -> None:
        if self._listener is not None:
            return
        if self.path.exists() or self.path.is_symlink():
            raise ProtocolTransportUnavailable("local protocol socket path exists")
        listener = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        try:
            listener.bind(os.fspath(self.path))
            bound = self.path.stat()
            self._owned_path_identity = (bound.st_dev, bound.st_ino)
            os.chmod(self.path, 0o600)
            listener.listen(1)
        except Exception:
            listener.close()
            self._unlink_owned_path()
            raise
        self._listener = listener

    def serve_once(self, *, timeout: float = DEFAULT_DEADLINE_SECONDS) -> bool:
        """Handle at most one request, closing malformed or failed peers."""
        if self._listener is None:
            raise ProtocolTransportUnavailable("local protocol server is not started")
        deadline = _deadline(timeout)
        _set_deadline(self._listener, deadline)
        try:
            peer, _address = self._listener.accept()
        except socket.timeout as error:
            raise ProtocolTransportTimeout(
                "local protocol accept timed out") from error
        try:
            _verify_peer(peer, self.expected_uid, self._peer_uid_reader)
            request = _receive_message(peer, deadline)
            response = self.handler(request)
            if not isinstance(response, ProtocolMessage):
                raise ProtocolTransportClosed("local protocol handler rejected")
            _send_message(peer, response, deadline)
            return True
        except (ProtocolTransportError, ValueError, TypeError, OSError):
            return False
        except Exception:
            # A handler crash receives no wire error or payload reflection.
            return False
        finally:
            peer.close()

    def close(self) -> None:
        listener, self._listener = self._listener, None
        if listener is not None:
            listener.close()
        self._unlink_owned_path()

    def _unlink_owned_path(self) -> None:
        """Remove only the exact filesystem socket inode created by start."""
        identity, self._owned_path_identity = self._owned_path_identity, None
        if identity is None:
            return
        try:
            current = self.path.stat()
            if (stat.S_ISSOCK(current.st_mode)
                    and (current.st_dev, current.st_ino) == identity):
                self.path.unlink()
        except OSError:
            pass

    def __enter__(self) -> "UnixProtocolServer":
        self.start()
        return self

    def __exit__(self, *_details: object) -> None:
        self.close()


def request(
    path: str | os.PathLike[str],
    message: ProtocolMessage,
    *,
    timeout: float = DEFAULT_DEADLINE_SECONDS,
    expected_uid: int | None = None,
    peer_uid_reader: Callable[[socket.socket], int | None] = _peer_uid,
) -> ProtocolMessage:
    """Send one canonical request and receive one canonical response locally."""
    _require_posix_unix_socket()
    if not isinstance(message, ProtocolMessage):
        raise TypeError("message must be a ProtocolMessage")
    if not callable(peer_uid_reader):
        raise TypeError("peer_uid_reader must be callable")
    expected_uid = os.geteuid() if expected_uid is None else expected_uid
    if (isinstance(expected_uid, bool) or not isinstance(expected_uid, int)
            or expected_uid < 0):
        raise ValueError("expected_uid must be a non-negative integer")
    deadline = _deadline(timeout)
    with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
        try:
            _set_deadline(peer, deadline)
            peer.connect(os.fspath(_socket_path(path)))
        except socket.timeout as error:
            raise ProtocolTransportTimeout(
                "local protocol connect timed out") from error
        except OSError as error:
            raise ProtocolTransportUnavailable(
                "local protocol socket is unavailable") from error
        _verify_peer(peer, expected_uid, peer_uid_reader)
        _send_message(peer, message, deadline)
        return _receive_message(peer, deadline)


__all__ = [
    "DEFAULT_DEADLINE_SECONDS",
    "MAX_DEADLINE_SECONDS",
    "MAX_SOCKET_PATH_CHARS",
    "ProtocolTransportClosed",
    "ProtocolTransportError",
    "ProtocolTransportPeerRejected",
    "ProtocolTransportTimeout",
    "ProtocolTransportUnavailable",
    "UnixProtocolServer",
    "request",
]
