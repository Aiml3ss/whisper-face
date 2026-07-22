import os
import stat
import sys
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from macos_networkless_worker import (  # noqa: E402
    MacOSNetworklessWorker,
    NetworklessWorkerRejected,
    SANDBOX_PROFILE,
    exchange_once,
    sandbox_primitive_available,
)
from voice_input_protocol import (  # noqa: E402
    MessageKind,
    VoiceInputProtocolSession,
    validate_transcript,
)


class MacOSNetworklessWorkerContractTests(unittest.TestCase):
    def test_profile_denies_by_default_and_limits_unix_ipc_to_private_root(self):
        profile = SANDBOX_PROFILE.read_text(encoding="utf-8")

        self.assertIn("(deny default)", profile)
        self.assertIn("(deny network*)", profile)
        self.assertIn('(subpath (param "IPC_ROOT"))', profile)
        self.assertIn("(deny process-fork)", profile)
        self.assertNotIn("(allow default)", profile)

    def test_runtime_entrypoints_do_not_activate_the_worker(self):
        for runtime in ("dictate.py", "whisper_face_gui.py"):
            with self.subTest(runtime=runtime):
                source = (ROOT / runtime).read_text(encoding="utf-8")
                self.assertNotIn("macos_networkless_worker", source)

    def test_transcript_bearing_message_is_rejected_before_launch(self):
        session = VoiceInputProtocolSession("worker-secret", "readable-complete")
        session.capture_proposal()
        transcript_message = session.publish_stable_prefix(
            "private transcript text", 100)
        worker = MacOSNetworklessWorker(timeout=0.5)
        self.addCleanup(worker.close)

        with self.assertRaises(NetworklessWorkerRejected) as caught:
            worker.exchange(transcript_message)

        self.assertEqual(str(caught.exception),
                         "networkless worker request rejected")
        self.assertNotIn("private transcript text", str(caught.exception))


@unittest.skipUnless(sys.platform == "darwin", "requires macOS")
class MacOSNetworklessWorkerIntegrationTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        if not sandbox_primitive_available():
            raise unittest.SkipTest("macOS sandbox primitive is unavailable")

    def message(self, utterance_id="sandbox-worker-001"):
        session = VoiceInputProtocolSession(
            utterance_id, "readable-complete")
        return session.capture_proposal()

    def test_worker_proves_network_denial_and_returns_content_free_receipt(self):
        request = self.message()
        response = exchange_once(request, timeout=1.0)

        self.assertEqual(response.kind, MessageKind.CANCELLATION)
        self.assertEqual(response.sequence, 1)
        self.assertEqual(dict(response.payload), {"reason": "capture_failed"})
        self.assertNotIn("text", response.payload)
        self.assertEqual(validate_transcript((request, response)),
                         (request, response))

    def test_ipc_directory_and_socket_are_private_and_bounded(self):
        worker = MacOSNetworklessWorker(timeout=1.0)
        self.addCleanup(worker.close)
        worker.start()
        socket_path = worker.socket_path

        self.assertLessEqual(len(os.fsencode(socket_path)), 100)
        self.assertEqual(stat.S_IMODE(socket_path.parent.stat().st_mode), 0o700)
        self.assertEqual(stat.S_IMODE(socket_path.stat().st_mode), 0o600)
        self.assertTrue(stat.S_ISSOCK(socket_path.stat().st_mode))

        response = worker.exchange(self.message("sandbox-worker-002"))
        self.assertEqual(response.kind, MessageKind.CANCELLATION)
        self.assertFalse(socket_path.parent.exists())


if __name__ == "__main__":
    unittest.main()
