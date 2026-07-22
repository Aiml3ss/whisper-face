import os
import socket
import stat
import struct
import sys
import tempfile
import threading
import time
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from voice_input_protocol import VoiceInputProtocolSession  # noqa: E402
from voice_input_protocol_transport import (  # noqa: E402
    MAX_FRAME_BYTES,
    ProtocolTransportClosed,
    ProtocolTransportPeerRejected,
    ProtocolTransportTimeout,
    UnixProtocolServer,
    request,
)


@unittest.skipUnless(
    os.name != "nt" and hasattr(socket, "AF_UNIX"),
    "requires POSIX Unix-domain sockets",
)
class VoiceInputProtocolTransportTests(unittest.TestCase):
    def setUp(self):
        self.directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.directory.cleanup)
        self.path = Path(self.directory.name) / "voice-input.sock"
        session = VoiceInputProtocolSession("transport-001", "readable-complete")
        self.message = session.capture_proposal()

    def serve_once(self, server):
        outcome = []
        worker = threading.Thread(
            target=lambda: outcome.append(server.serve_once(timeout=0.5)),
            daemon=True,
        )
        worker.start()
        return worker, outcome

    def test_canonical_round_trip_is_sequential_and_socket_is_private(self):
        received = []
        server = UnixProtocolServer(
            self.path, lambda message: received.append(message) or message)
        server.start()
        self.addCleanup(server.close)
        self.assertEqual(stat.S_IMODE(self.path.stat().st_mode), 0o600)

        worker, outcome = self.serve_once(server)
        response = request(self.path, self.message, timeout=0.5)
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, [True])
        self.assertEqual(received, [self.message])
        self.assertEqual(response, self.message)
        server.close()
        self.assertFalse(self.path.exists())

    def test_malformed_or_oversized_frames_close_without_a_response(self):
        server = UnixProtocolServer(self.path, lambda message: message)
        server.start()
        self.addCleanup(server.close)
        worker, outcome = self.serve_once(server)
        with socket.socket(socket.AF_UNIX, socket.SOCK_STREAM) as peer:
            peer.settimeout(0.5)
            peer.connect(os.fspath(self.path))
            peer.sendall(struct.pack("!I", MAX_FRAME_BYTES + 1))
            self.assertEqual(peer.recv(1), b"")
        worker.join(1)

        self.assertFalse(worker.is_alive())
        self.assertEqual(outcome, [False])

    def test_response_deadline_and_handler_crash_fail_closed(self):
        def delayed(_message):
            time.sleep(0.1)
            return self.message

        server = UnixProtocolServer(self.path, delayed)
        server.start()
        self.addCleanup(server.close)
        worker, outcome = self.serve_once(server)
        with self.assertRaises(ProtocolTransportTimeout):
            request(self.path, self.message, timeout=0.02)
        worker.join(1)
        self.assertEqual(outcome, [False])

        server.close()
        crashing = UnixProtocolServer(
            self.path, lambda _message: (_ for _ in ()).throw(RuntimeError()))
        crashing.start()
        self.addCleanup(crashing.close)
        worker, outcome = self.serve_once(crashing)
        with self.assertRaises(ProtocolTransportClosed):
            request(self.path, self.message, timeout=0.5)
        worker.join(1)
        self.assertEqual(outcome, [False])

    def test_supported_peer_identity_mismatch_is_rejected(self):
        server = UnixProtocolServer(
            self.path,
            lambda message: message,
            peer_uid_reader=lambda _peer: os.geteuid() + 1,
        )
        server.start()
        self.addCleanup(server.close)
        worker, outcome = self.serve_once(server)

        with self.assertRaises(ProtocolTransportClosed):
            request(self.path, self.message, timeout=0.5)
        worker.join(1)
        self.assertEqual(outcome, [False])

        with self.assertRaises(ProtocolTransportPeerRejected):
            request(
                self.path,
                self.message,
                timeout=0.5,
                peer_uid_reader=lambda _peer: os.geteuid() + 1,
            )

    def test_close_does_not_remove_a_replacement_socket_inode(self):
        server = UnixProtocolServer(self.path, lambda message: message)
        server.start()
        self.path.unlink()
        replacement = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
        self.addCleanup(replacement.close)
        replacement.bind(os.fspath(self.path))
        replacement.listen(1)

        server.close()

        self.assertTrue(self.path.exists())
        self.assertTrue(stat.S_ISSOCK(self.path.stat().st_mode))
        self.path.unlink()


if __name__ == "__main__":
    unittest.main()
