# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import ast
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

    def test_repository_makes_installer_parity_a_release_gate(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        process = (ROOT / "docs" / "installer-release-process.md").read_text(
            encoding="utf-8")
        pull_request = (
            ROOT / ".github" / "pull_request_template.md"
        ).read_text(encoding="utf-8")
        for required in (
            "uv lock --check --script dictate.py",
            "uv run tests/test_parrot_core.py",
            "uv run tests/test_voice_compiler.py",
            "uv run tests/test_consequence_routing.py",
            "uv run tests/test_benchmark_voice_compiler.py",
            "uv run tests/test_benchmark_consequence_routing.py",
            "uv run tests/test_benchmark_asr.py",
            "uv run tests/test_performance_lab.py",
            "uv run tests/test_dictate.py",
            "uv run tests/test_gui_settings_runtime.py",
            "uv run tests/test_insertion_integrity.py",
            "uv run tests/test_benchmark_insertion_reliability.py",
            "uv run tests/test_compatibility_fingerprint.py",
            "uv run tests/test_voice_input_protocol.py",
            "uv run tests/test_acoustic_keyword_memory.py",
            "uv run tests/test_delayed_cleanup_merge.py",
            "uv run tests/test_model_wallet.py",
            "uv run tests/test_point_and_speak_resolver.py",
            "uv run tests/test_drop_to_target.py",
            "uv run tests/test_voice_objects.py",
            "uv run tests/test_voice_inbox.py",
            "uv run tests/test_competitor_benchmark.py",
            "uv run tests/test_public_scorecard.py",
            "uv run tests/test_personal_regression.py",
            "uv run tests/test_whisper_face_gui.py",
            "uv run --locked --script dictate.py --native-gui-smoke-test",
            "uv run tests/test_installers.py",
            "uv run tests/test_macos_distribution.py",
            "setup.sh --verify",
            "setup.ps1 --verify",
        ):
            with self.subTest(required=required):
                self.assertIn(required, agents)
                self.assertIn(required, process)
        self.assertIn("Installer parity", pull_request)
        self.assertIn("distribution branch", agents)

    def test_cleanup_model_stays_in_sync_with_both_installers(self):
        tree = ast.parse(self.script)
        model = None
        for node in tree.body:
            if not isinstance(node, ast.Assign):
                continue
            if any(isinstance(target, ast.Name)
                   and target.id == "OLLAMA_MODEL" for target in node.targets):
                model = ast.literal_eval(node.value)
                break
        self.assertIsInstance(model, str)
        self.assertIn(model, self.shell)
        self.assertIn(model, self.powershell)

    def test_both_installers_use_current_runtime_and_preload_contract(self):
        for name, installer in (
            ("shell", self.shell), ("powershell", self.powershell)
        ):
            with self.subTest(installer=name):
                self.assertIn("dictate.py", installer)
                self.assertIn("parrot_core.py", installer)
                self.assertIn("voice_compiler.py", installer)
                self.assertIn("insertion_integrity.py", installer)
                self.assertIn("personal_regression.py", installer)
                self.assertIn("whisper_face_gui.py", installer)
                self.assertIn("dictate.py.lock", installer)
                self.assertIn("--preload-models", installer)
                self.assertIn("--verify", installer)
                self.assertIn("--verify-ollama-model", installer)

    def test_shell_dispatches_windows_before_mac_only_work(self):
        dispatch = self.shell.index("MINGW*|MSYS*|CYGWIN*")
        homebrew = self.shell.index("installing Homebrew")
        self.assertLess(dispatch, homebrew)
        self.assertIn("powershell.exe", self.shell[dispatch:homebrew])
        self.assertIn("wslpath -w", self.shell[dispatch:homebrew])

    def test_mac_installer_builds_and_verifies_native_parakeet_helper(self):
        for expected in (
            "native/ParrotASRHelper/Package.swift",
            "native/ParrotASRHelper/Package.resolved",
            "swift build -c release",
            "parrot-asr-helper",
            '"$parakeet_helper" --preload',
            '"$parakeet_helper" --verify',
            "--preload-parakeet-model",
            "--verify-parakeet-model",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.shell)
        self.assertIn("PARAKEET_HELPER", self.script)
        self.assertIn("PARROT_ASR_BACKEND", self.script)
        self.assertIn(
            "4252711f6f060f9a2f91e5f081a806d7f45eebd8", self.script)
        self.assertIn(
            "2a654d98e6fba55d452b7043684e9b57a947e393bbffa62485a7aac05ee4eefd",
            self.script,
        )

    def test_windows_keeps_independent_whisper_fallback(self):
        self.assertIn("Tiny -> Turbo", self.powershell)
        self.assertNotIn("swift build", self.powershell)
        self.assertNotIn("parrot-asr-helper", self.powershell)

    def test_native_gui_smoke_is_bounded_on_mac_and_static_on_windows(self):
        workflow = (
            ROOT / ".github" / "workflows" / "macos-release.yml"
        ).read_text(encoding="utf-8")
        gui = (ROOT / "whisper_face_gui.py").read_text(encoding="utf-8")
        command = "--native-gui-smoke-test"
        self.assertIn(command, self.script)
        self.assertIn(command, self.shell)
        self.assertIn(command, workflow)
        self.assertNotIn(command, self.powershell)
        self.assertIn("run_with_timeout 30", self.shell)
        self.assertIn('if [ "$MODE" = "full" ]', self.shell)
        self.assertIn("subprocess.run(sys.argv[1:], check=True, timeout=30)",
                      workflow)
        self.assertIn("native_appkit_smoke_contract", gui)
        self.assertIn("allowed_side_effects: tuple[str, ...] = ()", gui)
        self.assertIn("if not IS_MACOS", self.script)

    def test_whisper_face_assets_and_preference_ship_on_both_platforms(self):
        template = (ROOT / "preferences.template.json").read_text(
            encoding="utf-8")
        self.assertIn('"face": "parrot"', template)
        for face in ("parrot", "fox", "owl", "cat", "bear"):
            for frame in ("idle", "talk"):
                relative = f"icons/faces/{face}-{frame}.svg"
                with self.subTest(relative=relative):
                    self.assertTrue((ROOT / relative).exists())
                    self.assertIn(relative, self.shell)
                    self.assertIn(relative.replace("/", "\\"), self.powershell)
        for expected in (
            'APP_NAME = "Whisper Face"',
            'FACE_CHOICES = ("parrot", "fox", "owl", "cat", "bear")',
            "setMouthLevel_",
            "_draw_companion",
            "drawOwl_",
        ):
            self.assertIn(expected, self.script)

    def test_windows_installer_migrates_the_legacy_task_name(self):
        self.assertIn('$TaskName = "Whisper Face"', self.powershell)
        self.assertIn('$LegacyTaskName = "Whispering Parrot"', self.powershell)
        self.assertIn("Unregister-ScheduledTask", self.powershell)

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
