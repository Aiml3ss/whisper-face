# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


class InstallerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = (ROOT / "setup.sh").read_text(encoding="utf-8")
        cls.powershell = (ROOT / "setup.ps1").read_text(encoding="utf-8")
        cls.script = (ROOT / "dictate.py").read_text(encoding="utf-8")
        cls.lock = (ROOT / "dictate.py.lock").read_text(encoding="utf-8")

    def test_shell_dispatches_windows_before_mac_only_work(self):
        dispatch = self.shell.index("MINGW*|MSYS*|CYGWIN*")
        homebrew = self.shell.index("installing Homebrew")
        self.assertLess(dispatch, homebrew)
        self.assertIn("powershell.exe", self.shell[dispatch:homebrew])
        self.assertIn("wslpath -w", self.shell[dispatch:homebrew])

    def test_each_platform_has_a_clickable_entrypoint(self):
        self.assertTrue((ROOT / "Install.command").exists())
        self.assertTrue((ROOT / "Install.cmd").exists())
        self.assertIn(
            "setup.sh", (ROOT / "Install.command").read_text(encoding="utf-8"))
        self.assertIn(
            "setup.ps1", (ROOT / "Install.cmd").read_text(encoding="utf-8"))

    def test_windows_installer_covers_the_complete_stack(self):
        for expected in (
            "astral-sh.uv", "Gyan.FFmpeg", "Ollama.Ollama",
            "qwen3.5:4b", "--preload-models", "Register-ScheduledTask",
            "http://127.0.0.1:8787/health",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.powershell)

    def test_runtime_dependencies_are_platform_marked_and_locked(self):
        for dependency in (
            "mlx-whisper", "faster-whisper", "pyobjc-framework-Cocoa",
            "pywin32", "pystray",
        ):
            with self.subTest(dependency=dependency):
                self.assertRegex(
                    self.script,
                    rf'"{re.escape(dependency)}; sys_platform == \'(?:darwin|win32)\'"',
                )
        for package in ("mlx-whisper", "faster-whisper", "pywin32"):
            self.assertIn(f'name = "{package.lower()}"', self.lock.lower())

    def test_windows_runtime_uses_equivalent_models_and_native_service(self):
        self.assertIn('else "turbo"', self.script)
        self.assertIn('else "tiny"', self.script)
        self.assertIn("get_cuda_device_count", self.script)
        self.assertIn('options.append(("cpu", "int8"))', self.script)
        self.assertIn("WindowsStatusBar", self.script)
        self.assertIn("snapshot_windows_clipboard", self.script)


if __name__ == "__main__":
    unittest.main()
