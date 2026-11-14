# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import ast
import os
import plistlib
import re
import subprocess
import tempfile
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]

GATE_TEST = re.compile(r"uv run tests/(test_[a-z0-9_]+)\.py")
TEST_PATH = re.compile(r"tests/(test_[a-z0-9_]+)\.py")

# Files that hold the user's own words and corrections. Nothing here can be
# downloaded again, so an uninstall must keep every one of them unless the
# person running it asks, in as many words, for them to go.
PERSONAL_FILES = (
    "dictionary.txt",
    "snippets.json",
    "tones.json",
    "preferences.json",
    "learned.json",
    "transcripts.jsonl",
    "voice_inbox.json",
    "demonstrations.json",
    "acoustic_keyword_memory.json",
    "delayed_cleanup_activation.json",
    "acoustic_keyword_activation.json",
    "acoustic_calibration_activation.json",
    "relisten_activation.json",
    "dictate.log",
    "ollama.log",
    "install.log",
)

# Tools Whisper Face installs alongside itself but does not own. Someone else
# on this machine may depend on any of them.
SHARED_TOOLS = ("brew", "uv", "ffmpeg", "ollama")

# Tools a setup.sh function could reach that would change this machine or go
# to the network. Every one is shadowed by a stub that refuses, unless a test
# supplies its own, so a forgotten stub fails the test instead of the Mac.
REFUSED_TOOLS = (
    "brew", "uv", "ollama", "launchctl", "curl", "swift", "swiftc",
    "codesign", "open", "python3", "lsof",
)


def shell_function(name: str) -> str:
    """The source of one top-level setup.sh function, by name."""
    source = (ROOT / "setup.sh").read_text(encoding="utf-8")
    match = re.search(
        rf"(?ms)^{re.escape(name)}\(\) \{{\n.*?^\}}\n", source)
    if match is None:
        raise AssertionError(f"setup.sh defines no {name}() function")
    return match.group(0)


def run_setup_functions(names, script, *, variables=None, stubs=None,
                        cwd=None):
    """Run named setup.sh functions in isolation; never the installer.

    ``variables`` become shell variables ahead of the functions, the way the
    installer's own top level sets them. ``stubs`` maps a command name to a
    bash body that replaces that command on PATH.
    """
    with tempfile.TemporaryDirectory() as directory:
        bin_dir = Path(directory) / "bin"
        bin_dir.mkdir()
        bodies = {tool: 'echo "refused: $0 $*" >&2; exit 97'
                  for tool in REFUSED_TOOLS}
        bodies.update(stubs or {})
        for tool, body in bodies.items():
            stub = bin_dir / tool
            stub.write_text(f"#!/bin/bash\n{body}\n", encoding="utf-8")
            stub.chmod(0o755)
        assignments = "".join(
            f"{key}={_shell_quote(value)}\n"
            for key, value in (variables or {}).items())
        program = (
            "set -euo pipefail\n" + assignments
            + "".join(shell_function(name) for name in names) + script)
        environment = {
            "HOME": directory,
            "PATH": f"{bin_dir}:/usr/bin:/bin:/usr/sbin:/sbin",
            "LANG": "C",
            "STUB_LOG": str(Path(directory) / "calls.log"),
        }
        result = subprocess.run(
            ["/bin/bash", "-c", program], cwd=cwd or directory,
            env=environment, text=True, capture_output=True, timeout=60,
            stdin=subprocess.DEVNULL)
        calls_path = Path(directory) / "calls.log"
        result.calls = (calls_path.read_text(encoding="utf-8")
                        if calls_path.exists() else "")
        return result


def _shell_quote(value) -> str:
    return "'" + str(value).replace("'", "'\\''") + "'"


class InstallerContractTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.shell = (ROOT / "setup.sh").read_text(encoding="utf-8")
        cls.powershell = (ROOT / "setup.ps1").read_text(encoding="utf-8")
        cls.script = (ROOT / "dictate.py").read_text(encoding="utf-8")
        cls.lock = (ROOT / "dictate.py.lock").read_text(encoding="utf-8")

    def shell_uninstall(self):
        """The uninstall block of setup.sh, up to the install path proper."""
        start = self.shell.index("# --- Uninstall ---")
        return self.shell[start:self.shell.index("\nrequired=(", start)]

    def powershell_uninstall(self):
        """The uninstall block of setup.ps1, up to the install path proper."""
        start = self.powershell.index("# --- Uninstall ---")
        return self.powershell[
            start:self.powershell.index("\n$Required = @(", start)]

    def test_repository_makes_installer_parity_a_release_gate(self):
        agents = (ROOT / "AGENTS.md").read_text(encoding="utf-8")
        process = (ROOT / "docs" / "installer-release-process.md").read_text(
            encoding="utf-8")
        pull_request = (
            ROOT / ".github" / "pull_request_template.md"
        ).read_text(encoding="utf-8")
        macos_workflow = (
            ROOT / ".github" / "workflows" / "macos-release.yml"
        ).read_text(encoding="utf-8")
        windows_workflow = (
            ROOT / ".github" / "workflows" / "windows-smoke.yml"
        ).read_text(encoding="utf-8")
        for required in (
            "uv lock --check --script dictate.py",
            "uv run tests/test_parrot_core.py",
            "uv run tests/test_voice_compiler.py",
            "uv run tests/test_consequence_routing.py",
            "uv run tests/test_cleanup_circuit_breaker.py",
            "uv run tests/test_process_verifier.py",
            "uv run tests/test_prewarmed_verifier.py",
            "uv run tests/test_whisper_verifier_adapter.py",
            "uv run tests/test_prewarmed_whisper_verifier_adapter.py",
            "uv run tests/test_benchmark_relisten_activation.py",
            "uv run tests/test_benchmark_voice_compiler.py",
            "uv run tests/test_benchmark_consequence_routing.py",
            "uv run tests/test_benchmark_cleanup_latency.py",
            "uv run tests/test_cleanup_proof_recovery.py",
            "uv run tests/test_benchmark_cleanup_proof_recovery.py",
            "uv run tests/test_benchmark_asr.py",
            "uv run tests/test_benchmark_macos_asr_warm_path.py",
            "uv run tests/test_performance_lab.py",
            "uv run tests/test_dictate.py",
            "uv run tests/test_gui_settings_runtime.py",
            "uv run tests/test_insertion_integrity.py",
            "uv run tests/test_benchmark_insertion_reliability.py",
            "uv run tests/test_compatibility_fingerprint.py",
            "uv run tests/test_voice_input_protocol.py",
            "uv run tests/test_voice_input_protocol_wire.py",
            "uv run tests/test_voice_input_protocol_transport.py",
            "uv run tests/test_macos_networkless_worker.py",
            "uv run tests/test_acoustic_keyword_memory.py",
            "uv run tests/test_acoustic_keyword_bias_evaluation.py",
            "uv run tests/test_acoustic_keyword_activation.py",
            "uv run tests/test_acoustic_time_machine.py",
            "uv run tests/test_acoustic_calibration.py",
            "uv run tests/test_acoustic_calibration_activation.py",
            "uv run tests/test_benchmark_acoustic_calibration.py",
            "uv run tests/test_delayed_cleanup_merge.py",
            "uv run tests/test_macos_delayed_cleanup_destination.py",
            "uv run tests/test_model_wallet.py",
            "uv run tests/test_model_wallet_shadow.py",
            "uv run tests/test_model_readiness_evidence.py",
            "uv run tests/test_point_and_speak_resolver.py",
            "uv run tests/test_macos_point_and_speak_snapshot.py",
            "uv run tests/test_macos_drop_to_target_snapshot.py",
            "uv run tests/test_drop_to_target.py",
            "uv run tests/test_voice_objects.py",
            "uv run tests/test_voice_object_command_parser.py",
            "uv run tests/test_voice_object_commands_runtime.py",
            "uv run tests/test_spoken_edit_commands_runtime.py",
            "uv run tests/test_voice_inbox.py",
            "uv run tests/test_voice_object_inbox_bridge.py",
            "uv run tests/test_risky_action_confirmation.py",
            "uv run tests/test_demonstration_drafts.py",
            "uv run tests/test_competitor_benchmark.py",
            "uv run tests/test_public_scorecard.py",
            "uv run tests/test_personal_regression.py",
            "uv run tests/test_support_bundle.py",
            "uv run tests/test_whisper_face_gui.py",
            "uv run tests/test_whisper_face_characters.py",
            "uv run --locked --script dictate.py --native-gui-smoke-test",
            "uv run tests/test_network_egress.py",
            "uv run tests/test_supply_chain_integrity.py",
            "uv run tests/test_installers.py",
            "uv run tests/test_macos_distribution.py",
            "uv run tests/test_safe_update_advisor.py",
            "uv run tests/test_side_by_side_update.py",
            "uv run tests/test_self_update.py",
            "setup.sh --verify",
            "setup.ps1 --verify",
        ):
            with self.subTest(required=required):
                self.assertIn(required, agents)
                self.assertIn(required, process)
        self.assertIn("Installer parity", pull_request)
        self.assertIn("distribution branch", agents)
        for batch_gate in (
            "uv run tests/test_cleanup_circuit_breaker.py",
            "uv run tests/test_benchmark_cleanup_latency.py",
            "uv run tests/test_cleanup_proof_recovery.py",
            "uv run tests/test_benchmark_cleanup_proof_recovery.py",
            "uv run tests/test_benchmark_macos_asr_warm_path.py",
            "uv run tests/test_macos_drop_to_target_snapshot.py",
            "uv run tests/test_macos_delayed_cleanup_destination.py",
            "uv run tests/test_acoustic_keyword_bias_evaluation.py",
            "uv run tests/test_acoustic_calibration.py",
            "uv run tests/test_benchmark_acoustic_calibration.py",
            "uv run tests/test_model_wallet_shadow.py",
            "uv run tests/test_model_readiness_evidence.py",
            "uv run tests/test_demonstration_drafts.py",
            "uv run tests/test_support_bundle.py",
            "uv run tests/test_safe_update_advisor.py",
            "uv run tests/test_side_by_side_update.py",
            "uv run tests/test_self_update.py",
            "uv run tests/test_spoken_edit_commands_runtime.py",
            "uv run tests/test_whisper_face_characters.py",
            "uv run tests/test_acoustic_keyword_activation.py",
            "uv run tests/test_acoustic_calibration_activation.py",
            "uv run tests/test_network_egress.py",
            "uv run tests/test_supply_chain_integrity.py",
        ):
            with self.subTest(batch_gate=batch_gate):
                self.assertIn(batch_gate, pull_request)
                self.assertIn(batch_gate, macos_workflow)
                self.assertIn(batch_gate, windows_workflow)

    def test_the_three_full_gate_lists_hold_exactly_the_same_tests(self):
        # The list is duplicated so a contributor, the process document, and
        # release CI cannot disagree about what "the gates" means. Compare the
        # sets rather than the text so grouping stays a local decision.
        process = (ROOT / "docs" / "installer-release-process.md").read_text(
            encoding="utf-8")
        gate_section = process[:process.index("## Deliberate gate exclusions")]
        lists = {
            "AGENTS.md": set(GATE_TEST.findall(
                (ROOT / "AGENTS.md").read_text(encoding="utf-8"))),
            "docs/installer-release-process.md": set(
                GATE_TEST.findall(gate_section)),
            "macos-release.yml": set(GATE_TEST.findall((
                ROOT / ".github" / "workflows" / "macos-release.yml"
            ).read_text(encoding="utf-8"))),
        }
        canonical = lists["AGENTS.md"]
        self.assertGreater(len(canonical), 50)
        for name, gated in lists.items():
            with self.subTest(gate_list=name):
                self.assertEqual(
                    gated, canonical,
                    f"{name} has drifted from the other gate lists: "
                    f"only here {sorted(gated - canonical)}, "
                    f"missing here {sorted(canonical - gated)}")

        # Windows CI and the pull-request checklist carry the same
        # platform-independent subset as each other, never something extra.
        windows = set(GATE_TEST.findall((
            ROOT / ".github" / "workflows" / "windows-smoke.yml"
        ).read_text(encoding="utf-8")))
        pull_request = set(GATE_TEST.findall((
            ROOT / ".github" / "pull_request_template.md"
        ).read_text(encoding="utf-8")))
        self.assertEqual(windows, pull_request)
        self.assertTrue(
            windows <= canonical,
            f"Windows CI gates something absent from the full list: "
            f"{sorted(windows - canonical)}")

    def test_every_test_file_is_gated_or_documented_as_an_exclusion(self):
        # A new test file must force a decision. Either it protects a shipping
        # contract and joins the gate list, or the reason it does not is
        # written down where the next person will read it.
        process = (ROOT / "docs" / "installer-release-process.md").read_text(
            encoding="utf-8")
        boundary = process.index("## Deliberate gate exclusions")
        gated = set(GATE_TEST.findall(process[:boundary]))
        section = process[boundary:process.index("\n## ", boundary + 1)]
        excluded = set(TEST_PATH.findall("\n".join(
            row for row in section.splitlines()
            if row.startswith("| `tests/"))))
        self.assertFalse(
            gated & excluded,
            f"listed as both gated and excluded: {sorted(gated & excluded)}")
        for path in sorted((ROOT / "tests").glob("test_*.py")):
            with self.subTest(test=path.name):
                self.assertIn(
                    path.stem, gated | excluded,
                    f"{path.name} is in no gate list and is not documented "
                    f"under 'Deliberate gate exclusions'")
        for name in sorted(gated | excluded):
            with self.subTest(named=name):
                self.assertTrue(
                    (ROOT / "tests" / f"{name}.py").is_file(),
                    f"{name}.py is named in the release process but does "
                    f"not exist")

    def test_both_installers_offer_a_way_out(self):
        # Leaving cleanly is a trust property. Both platforms expose the same
        # four-word vocabulary, and neither removes anything until asked.
        for name, installer in (
            ("shell", self.shell), ("powershell", self.powershell)
        ):
            for flag in ("--uninstall", "--yes", "--remove-models",
                         "--remove-personal-data"):
                with self.subTest(installer=name, flag=flag):
                    self.assertIn(flag, installer)
        # Mac: dry run is the default, and each destructive tier is off until
        # its own flag turns it on.
        for default in ("UNINSTALL=0", "ASSUME_YES=0", "REMOVE_MODELS=0",
                        "REMOVE_PERSONAL=0"):
            with self.subTest(default=default):
                self.assertIn(default, self.shell)
        self.assertIn('--uninstall) UNINSTALL=1 ;;', self.shell)
        self.assertIn('if [ "$UNINSTALL" -eq 1 ]; then\n'
                      '    if uninstall_whisper_face; then', self.shell)
        self.assertIn("(dry run)", self.shell)
        # Windows: same defaults, same dry run.
        for default in ("$Uninstall = $false", "$AssumeYes = $false",
                        "$RemoveModels = $false",
                        "$RemovePersonalData = $false"):
            with self.subTest(default=default):
                self.assertIn(default, self.powershell)
        self.assertIn("(dry run)", self.powershell)
        self.assertIn("if ($Uninstall) {", self.powershell)

        # An uninstall must survive a partial installation. Both dispatch
        # before the repository-completeness check, so a checkout missing a
        # runtime file can still be uninstalled instead of erroring.
        self.assertLess(
            self.shell.index('if [ "$UNINSTALL" -eq 1 ]; then\n    if '),
            self.shell.index("\nrequired=("))
        self.assertLess(
            self.powershell.index("if ($Uninstall) {\n    $UninstallStatus"),
            self.powershell.index("\n$Required = @("))

    def test_uninstall_keeps_personal_files_unless_explicitly_asked(self):
        shell = self.shell_uninstall()
        powershell = self.powershell_uninstall()
        # Every personal file is listed, and listed only behind its own flag.
        for personal in PERSONAL_FILES:
            with self.subTest(personal=personal):
                self.assertIn(personal, shell)
                self.assertIn(personal, powershell)
        mac_personal = shell[shell.index('step "personal files'):]
        mac_personal = mac_personal[:mac_personal.index('step "never touched')]
        windows_personal = powershell[
            powershell.index('Write-Step "personal files'):]
        windows_personal = windows_personal[
            :windows_personal.index('Write-Step "never touched')]
        for personal in PERSONAL_FILES:
            with self.subTest(personal=personal, tier="personal"):
                self.assertIn(personal, mac_personal)
                self.assertIn(personal, windows_personal)
        self.assertIn('remove_installed_path "$REMOVE_PERSONAL"', mac_personal)
        self.assertIn("Remove-InstalledPath $RemovePersonalData",
                      windows_personal)
        # No personal file may be reachable from an unconditional removal.
        unconditional = re.findall(
            r'remove_installed_path 1 "([^"]+)"', shell)
        unconditional += re.findall(
            r'Remove-InstalledPath \$true \(?([^)\n]+)', powershell)
        for personal in PERSONAL_FILES:
            for target in unconditional:
                with self.subTest(personal=personal, target=target):
                    self.assertNotIn(personal, target)
        # The prompt has to say plainly what these files are.
        for installer in (mac_personal, windows_personal):
            self.assertIn("your words and your corrections", installer)
            self.assertIn("No download restores", installer)

    def test_uninstall_never_touches_a_shared_tool(self):
        shell = self.shell_uninstall()
        powershell = self.powershell_uninstall()
        for forbidden in (
            "brew uninstall", "brew remove", "winget uninstall",
            "rm -rf ~/.ollama", 'rm -rf "$HOME/.ollama"',
            "uv cache clean", "pip uninstall",
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, shell)
                self.assertNotIn(forbidden, powershell)
        # Nothing is ever matched by pattern; every target is spelled out.
        for wildcard in ("rm -rf *", 'rm -rf "$DIR"/*', "-Recurse -Include",
                         "Get-ChildItem -Filter"):
            with self.subTest(wildcard=wildcard):
                self.assertNotIn(wildcard, shell)
                self.assertNotIn(wildcard, powershell)
        for line in (shell + powershell).splitlines():
            statement = line.split("#", 1)[0]
            if "rm -rf" in statement or "rmdir" in statement \
                    or "Remove-Item" in statement:
                with self.subTest(statement=statement.strip()):
                    self.assertNotIn("*", statement)
        # Both say so out loud, so the person reading the output knows.
        for tool in SHARED_TOOLS:
            with self.subTest(tool=tool):
                self.assertIn(tool, shell[shell.index('step "never touched'):])
        self.assertIn(
            "astral-sh.uv, Gyan.FFmpeg, and Ollama.Ollama",
            powershell[powershell.index('Write-Step "never touched'):])
        # macOS privacy grants live in a system database an installer must not
        # edit; the user is told to revoke them by hand.
        self.assertIn("Privacy & Security", shell)
        self.assertIn("An installer must not edit that database", shell)
        for forbidden in ("tccutil", "sqlite3", "TCC.db"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, self.shell)

    def test_uninstall_inventory_covers_everything_the_installer_creates(self):
        shell = self.shell_uninstall()
        powershell = self.powershell_uninstall()

        # Every path the Mac install path writes is named by the uninstall.
        for variable in (
            "$dictate_plist", "$ollama_plist", "$launcher_app",
            "$launcher_receipt", "$ollama_service_receipt",
            "$service_receipt_dir", "$parakeet_helper",
        ):
            with self.subTest(variable=variable):
                self.assertIn(variable, shell)
        for built in (".models/bin", ".models/swift-build", ".dictate.lock",
                      "dictate.log", "ollama.log"):
            with self.subTest(built=built):
                self.assertIn(built, shell)
        # Both launchd jobs are unloaded before their definitions go.
        self.assertIn("launchctl bootout", shell)
        for label in ("com.berg.dictate", "com.berg.ollama"):
            with self.subTest(label=label):
                self.assertIn(f"unload_installed_agent {label}", shell)
        self.assertLess(shell.index("unload_installed_agent com.berg.dictate"),
                        shell.index('remove_installed_path 1 "$dictate_plist"'))

        # The private files the install path creates or protects, read out of
        # setup.sh itself, must all appear in the uninstall inventory.
        templates = re.search(
            r"for name in ([a-z_ ]+); do", self.shell).group(1).split()
        created = [f"{name}.json" for name in templates
                   if name != "dictionary"]
        created.append("dictionary.txt")
        protected = self.shell[
            self.shell.index("for private_file in transcripts.jsonl"):]
        protected = protected[:protected.index("; do")]
        created += re.findall(
            r"[A-Za-z0-9_.]+\.(?:jsonl|json|log|txt|lock)", protected)
        self.assertIn("transcripts.jsonl", created)
        self.assertIn(".dictate.lock", created)
        for name in sorted(set(created)):
            with self.subTest(created=name):
                self.assertIn(
                    name, shell,
                    f"setup.sh creates or protects {name} but the uninstall "
                    f"inventory never mentions it")

        # Windows: the login task under both names, the generated launcher and
        # its receipt directory, and the model cache.
        for expected in (
            "$TaskName", "$LegacyTaskName", "$Launcher", "$LauncherReceipt",
            "$LauncherDir", ".models", ".dictate.lock",
            "Unregister-ScheduledTask",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, powershell)
        windows_protected = self.powershell[
            self.powershell.index("foreach ($PrivateStateName in @("):]
        windows_protected = windows_protected[:windows_protected.index("))")]
        for name in re.findall(r'"([A-Za-z0-9_.]+\.json)"', windows_protected):
            with self.subTest(created=name):
                self.assertIn(name, powershell)
        for log in ("dictate.log", "ollama.log", "ollama-error.log"):
            with self.subTest(log=log):
                self.assertIn(log, powershell)

        # Models are a separate, larger decision on both platforms.
        for model_path in (
            "FluidAudio/Models/parakeet-unified-en-0.6b",
            "models--mlx-community--whisper-tiny",
            "models--mlx-community--whisper-large-v3-turbo",
        ):
            with self.subTest(model_path=model_path):
                self.assertIn(model_path, shell)
        self.assertIn('remove_installed_path "$REMOVE_MODELS"', shell)
        self.assertIn("Remove-InstalledPath $RemoveModels", powershell)
        self.assertIn("ollama rm qwen3.5:4b", shell)
        self.assertIn("ollama rm qwen3.5:4b", powershell)

    def test_uninstall_is_idempotent_and_honest_about_what_it_kept(self):
        shell = self.shell_uninstall()
        powershell = self.powershell_uninstall()
        # A missing target is reported, never an error.
        for required in ('"not present"', '"keeping"'):
            with self.subTest(required=required):
                self.assertIn(required, shell)
                self.assertIn(required, powershell)
        # Only macOS has launchd jobs to find already unloaded.
        self.assertIn('"not loaded"', shell)
        self.assertIn('if [ ! -e "$target" ] && [ ! -L "$target" ]; then',
                      shell)
        self.assertIn("if (-not $Present) {", powershell)
        # A shared parent directory is removed only when already empty.
        self.assertIn("rmdir -- ", shell)
        self.assertIn("still holds other files", shell)
        self.assertIn("still holds other files", powershell)
        # Refuse an unsafe root outright rather than trusting the caller.
        self.assertIn('refusing to remove an unsafe path', shell)
        self.assertIn("refusing to remove an unsafe path", powershell)
        # The closing summary names what survived and where it lives.
        for kept in ("models were kept", "were kept in"):
            with self.subTest(kept=kept):
                self.assertIn(kept, shell)
                self.assertIn(kept, powershell)

    def test_uninstall_is_documented_where_install_is(self):
        readme = (ROOT / "README.md").read_text(encoding="utf-8")
        guide = (
            ROOT / "docs" / "distribution" / "update-and-rollback.md"
        ).read_text(encoding="utf-8")
        for expected in (
            "./setup.sh --uninstall",
            ".\\setup.ps1 --uninstall",
            "--remove-models",
            "--remove-personal-data",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, readme)
                self.assertIn(expected, guide)
        self.assertIn("## Uninstall", readme)

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

    def test_mac_installer_probes_before_announcing_any_model_download(self):
        # Hugging Face and Ollama both skip files they already have, so a size
        # may only be printed for a model this run is really going to fetch.
        self.assertIn("--model-inventory", self.shell)
        self.assertIn("--model-inventory", self.script)
        inventory = self.shell.index('refresh_model_inventory() {')
        for step in (
            'step "downloading the pinned Parakeet Unified model',
            'step "downloading Whisper Tiny',
            'step "downloading Whisper large-v3-turbo',
            'step "downloading qwen3.5:4b',
        ):
            with self.subTest(step=step):
                self.assertLess(inventory, self.shell.index(step))
        for probe, announcement in (
            ('[ "$(model_state parakeet)" = "present" ]',
             'step "Parakeet Unified model already installed'),
            ('[ "$(model_state whisper-fast)" = "present" ]',
             'step "Whisper Tiny already installed'),
            ('[ "$(model_state whisper-large)" = "present" ]',
             'step "Whisper large-v3-turbo already installed'),
            ('if qwen_is_installed; then',
             'echo "== qwen3.5:4b already installed"'),
        ):
            with self.subTest(probe=probe):
                self.assertIn(probe, self.shell)
                self.assertLess(
                    self.shell.index(probe), self.shell.index(announcement))
        # The old unconditional claim printed a download that a warm rerun
        # never performed.
        self.assertNotIn("downloading both Whisper models", self.shell)

    def test_mac_default_install_skips_the_optional_quality_models(self):
        for flag in ("--with-all-models", "--models", "--preload-fast-model"):
            with self.subTest(flag=flag):
                self.assertIn(flag, self.shell)
        self.assertIn("--preload-fast-model", self.script)
        self.assertIn("WITH_ALL_MODELS=0", self.shell)
        self.assertIn("--models) MODELS_ONLY=1; WITH_ALL_MODELS=1 ;;",
                      self.shell)

        # Each heavy download is reachable only through the opt-in flag.
        for download in (
            '"$OLLAMA" pull qwen3.5:4b',
            'step "downloading Whisper large-v3-turbo (~1.6 GB)"',
        ):
            with self.subTest(download=download):
                guard = self.shell.rindex(
                    'elif [ "$WITH_ALL_MODELS" -eq 1 ]; then',
                    0, self.shell.index(download))
                self.assertLess(guard, self.shell.index(download))
        # The always-required models stay unconditional.
        for required in (
            '"$UV" run --locked --script "$DIR/dictate.py" '
            '--preload-parakeet-model',
            '"$UV" run --locked --script "$DIR/dictate.py" '
            '--preload-fast-model',
        ):
            with self.subTest(required=required):
                self.assertIn(f'\n{required}\n', self.shell)
        # The closing hint names what was skipped and how to get it.
        self.assertIn("skipped_models+=(", self.shell)
        self.assertIn(
            'echo "== Dictation works without them. '
            'Add them with: ./setup.sh --models"',
            self.shell)
        self.assertIn("install_optional_models()", self.shell)
        self.assertIn('if [ "$MODELS_ONLY" -eq 1 ]; then', self.shell)

    def test_minimal_mac_install_is_a_supported_verified_configuration(self):
        verify = (shell_function("verify_cleanup_service")
                  + shell_function("verify_install"))
        # An absent optional cleanup model is a supported state, not a failure.
        self.assertNotIn('fail "qwen3.5:4b is not installed"', self.shell)
        self.assertIn(
            'if "$ollama_bin" show qwen3.5:4b >/dev/null 2>&1; then\n'
            '        verify_qwen=1\n',
            verify)
        manifest = verify.index("--verify-ollama-model")
        guard = verify.rindex('if [ "$verify_qwen" -eq 1 ]; then', 0, manifest)
        self.assertLess(guard, manifest)
        self.assertIn("cleanup stays deterministic", verify)
        # Installing the model keeps its manifest audit mandatory.
        self.assertIn(
            'fail "Qwen model manifest verification failed; rerun ./setup.sh',
            verify)
        self.assertIn("--verify-ollama-model",
                      shell_function("qwen_matches_audit"))
        install = self.shell[self.shell.index(
            'step "installing the locked Python environment"'):]
        install_manifest = install.index("qwen_matches_audit")
        self.assertLess(
            install.rindex('if [ "$qwen_installed" -eq 1 ]',
                           0, install_manifest),
            install_manifest)

    def test_runtime_degrades_when_an_optional_model_is_absent(self):
        # Recognition falls back to a model that exists instead of failing the
        # utterance, and cleanup falls back to the deterministic compiler.
        for required in (
            "def asr_model_is_cached(",
            "def asr_decode_target(",
            "model_repo = asr_decode_target(model_repo)",
            "ASR_MODELS_NOT_CACHED",
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.script)
        tree = ast.parse(self.script)
        warmup = next(
            node for node in tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "warmup")
        handlers = [
            handler for node in ast.walk(warmup)
            if isinstance(node, ast.Try) for handler in node.handlers
            if "Ollama warmup failed" in ast.dump(handler)
        ]
        self.assertEqual(len(handlers), 1)
        self.assertIn("cleanup_status", ast.dump(handlers[0]))
        self.assertFalse(any(
            isinstance(statement, ast.Raise)
            for statement in ast.walk(handlers[0])))

    def test_warm_rerun_reuses_only_an_exact_healthy_ollama_service(self):
        # macOS renders the desired plist on every run and may retain the warm
        # process only when config, launchd state, and health all agree.
        service = shell_function("start_ollama_service")
        branch_start = service.index('if ollama_service_identity_is_valid ')
        branch_end = service.index('echo -n "== waiting for Ollama"')
        branch = service[branch_start:branch_end]
        self.assertIn(
            'render_plist "$DIR/com.berg.ollama.plist.template"',
            service[:branch_start])
        for required in (
            'ollama_service_identity_is_valid "$desired_ollama_plist"',
            '"$ollama_plist" "$ollama_service_receipt"',
            '"$desired_ollama_digest"',
            'mv -f "$desired_ollama_plist" "$ollama_plist"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, branch)
        reuse = branch.index(
            "reusing healthy Ollama service (configuration unchanged)")
        reload = branch.index('reload_agent com.berg.ollama "$ollama_plist"')
        self.assertLess(reuse, reload)
        # The wait must poll the identity the next line asserts. A reload keeps
        # the previous process bound to 11434 while its replacement starts, so
        # two listeners briefly exist; ollama_listener_pid refuses a non-unique
        # result. Waiting on /api/tags alone broke out during that window and
        # hard-failed the install -- a rollback when run from the self-updater.
        wait = service[branch_end:service.index(
            'fail "Ollama did not become ready')]
        self.assertIn('if ollama_process_identity_is_valid; then', wait)
        self.assertNotIn('curl -fsS --max-time 1 http://127.0.0.1:11434', wait)
        self.assertIn('ollama-service.sha256', self.shell)
        for required in (
            'install -d -m 700 "$service_receipt_dir"',
            'chmod 600 "$receipt_temporary"',
            'mv -f "$receipt_temporary" "$ollama_service_receipt"',
            'fail "Ollama service identity could not be verified"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, service[branch_end:])

        # Windows has a health-based warm-service path, but its detached Ollama
        # process is not owned by the Whisper Face scheduled task. Do not claim
        # the launchd-equivalent loaded-service identity guarantee there.
        start = self.powershell.index(
            'Start-Process -FilePath $Ollama -ArgumentList "serve"')
        self.assertIn(
            'if (-not (Test-Endpoint "http://127.0.0.1:11434/api/tags"))',
            self.powershell[:start],
        )
        for installer in (self.shell, self.powershell):
            with self.subTest(installer="manifest verification"):
                self.assertIn("--verify-ollama-model", installer)
                self.assertIn("show", installer)

    def ollama_sandbox(self, directory, *, listener="", healthy=False):
        """Variables and stubs for running start_ollama_service offline."""
        root = Path(directory)
        (root / "LaunchAgents").mkdir()
        (root / "com.berg.ollama.plist.template").write_bytes(
            (ROOT / "com.berg.ollama.plist.template").read_bytes())
        loaded = root / "loaded"
        launchctl = (
            f'echo "launchctl $*" >> "$STUB_LOG"\n'
            f'case "$1" in\n'
            f'  print) [ -e {loaded} ] || exit 113\n'
            f'         printf "state = running\\n\\tpid = 77\\n" ;;\n'
            f'  bootstrap) touch {loaded} ;;\n'
            f'  bootout) rm -f {loaded} ;;\n'
            f'esac\n')
        variables = {
            "DIR": root,
            "VERIFY_ONLY": 0,
            "OLLAMA": "/opt/fake/bin/ollama",
            "ollama_plist": root / "LaunchAgents" / "com.berg.ollama.plist",
            "service_receipt_dir": root / "state",
            "ollama_service_receipt": root / "state" / "ollama-service.sha256",
        }
        stubs = {
            "launchctl": launchctl,
            "lsof": f'echo "lsof $*" >> "$STUB_LOG"; '
                    f'[ -z "{listener}" ] || echo {listener}',
            "ps": 'echo ollama',
            "sleep": ':',
            "curl": 'exit 0' if healthy else 'exit 7',
            "brew": 'echo "brew $*" >> "$STUB_LOG"',
        }
        return variables, stubs

    OLLAMA_FUNCTIONS = (
        "fail", "step", "escape_sed_replacement", "render_plist",
        "agent_is_running", "agent_pid", "ollama_listener_pid",
        "valid_sha256_digest", "ollama_process_identity_is_valid",
        "ollama_service_identity_is_valid", "reload_agent",
        "ollama_port_holder", "retire_ollama_service", "start_ollama_service",
    )

    def test_a_default_install_brings_no_ollama(self):
        # Ollama exists only to serve the optional cleanup model. A default
        # install must not install, load, or verify it.
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            plist = root / "com.berg.ollama.plist"
            manifest = root / "manifests" / "qwen3.5" / "4b"
            cases = (
                ("default install", 0, False, False, "uv ffmpeg"),
                ("cleanup model requested", 1, False, False,
                 "uv ffmpeg ollama"),
                ("earlier opt-in, model present", 0, True, True,
                 "uv ffmpeg ollama"),
                ("earlier service, no model", 0, True, False, "uv ffmpeg"),
            )
            for name, requested, has_plist, has_model, packages in cases:
                with self.subTest(case=name):
                    plist.unlink(missing_ok=True)
                    manifest.unlink(missing_ok=True)
                    if has_plist:
                        plist.write_text("x")
                    if has_model:
                        manifest.parent.mkdir(parents=True, exist_ok=True)
                        manifest.write_text("x")
                    result = run_setup_functions(
                        ["cleanup_model_wanted", "required_brew_packages"],
                        "required_brew_packages\n",
                        variables={"WITH_ALL_MODELS": requested,
                                   "ollama_plist": plist,
                                   "qwen_manifest": manifest})
                    self.assertEqual(result.returncode, 0, result.stderr)
                    self.assertEqual(result.stdout.strip(), packages)
            # Nor does --verify ask for it when no service was set up.
            plist.unlink()
            result = run_setup_functions(
                ["fail", "verify_cleanup_service"],
                'verify_cleanup_service\necho "verify_qwen=$verify_qwen"\n',
                variables={"VERIFY_ONLY": 1, "ollama_plist": plist})
            self.assertEqual(result.returncode, 0, result.stdout)
            self.assertEqual(result.stdout.strip(), "verify_qwen=0")
            self.assertNotIn("refused", result.stderr)

    def test_setup_never_stops_or_fights_someone_elses_ollama(self):
        with tempfile.TemporaryDirectory() as directory:
            variables, stubs = self.ollama_sandbox(directory, listener=4242)
            result = run_setup_functions(
                self.OLLAMA_FUNCTIONS, "start_ollama_service\n",
                variables=variables, stubs=stubs)
            self.assertEqual(result.returncode, 1)
            self.assertIn("port 11434 is in use by process 4242 (ollama)",
                          result.stderr)
            self.assertNotIn("brew services", result.calls)
            self.assertNotIn("launchctl bootstrap", result.calls)
            self.assertFalse(variables["ollama_plist"].exists())
            self.assertEqual(
                [path.name for path in Path(directory, "LaunchAgents").iterdir()],
                [])

        # An earlier install's agent cannot bind beside that Ollama either,
        # and KeepAlive would restart it forever. It is this installer's own,
        # so it is unloaded, and a rerun then installs without the model.
        with tempfile.TemporaryDirectory() as directory:
            variables, stubs = self.ollama_sandbox(directory, listener=4242)
            variables["ollama_plist"].write_text("earlier", encoding="utf-8")
            Path(directory, "loaded").touch()
            result = run_setup_functions(
                self.OLLAMA_FUNCTIONS, "start_ollama_service\n",
                variables=variables, stubs=stubs)
            self.assertEqual(result.returncode, 1)
            self.assertIn("port 11434 is in use by process 4242 (ollama)",
                          result.stderr)
            self.assertIn("launchctl bootout", result.calls)
            self.assertNotIn("launchctl bootstrap", result.calls)
            self.assertFalse(Path(directory, "loaded").exists())
            self.assertFalse(variables["ollama_plist"].exists())

    def test_an_ollama_service_that_never_becomes_ready_is_unloaded(self):
        # KeepAlive would otherwise restart it every ten seconds, at every
        # login, for as long as the plist stayed in ~/Library/LaunchAgents.
        with tempfile.TemporaryDirectory() as directory:
            variables, stubs = self.ollama_sandbox(directory)
            result = run_setup_functions(
                self.OLLAMA_FUNCTIONS, "start_ollama_service\n",
                variables=variables, stubs=stubs)
            self.assertEqual(result.returncode, 1)
            self.assertIn("Ollama did not become ready", result.stderr)
            calls = result.calls.splitlines()
            bootstrap = calls.index(next(
                call for call in calls if call.startswith("launchctl bootstrap")))
            self.assertTrue(any(call.startswith("launchctl bootout")
                                for call in calls[bootstrap:]))
            self.assertFalse(Path(directory, "loaded").exists())
            self.assertFalse(variables["ollama_plist"].exists())
            self.assertFalse(variables["ollama_service_receipt"].exists())

    def test_a_republished_cleanup_model_is_set_aside_by_models(self):
        # ./setup.sh --models pulls qwen3.5:4b by a tag its publisher can
        # move. A copy that fails the manifest audit must not leave a loaded
        # agent, its plist, or the model behind: plist plus model reads as an
        # opt-in, and every later setup, update and --verify would then fail.
        cases = (
            ("pulled by this run", False, True),
            ("already in the store", True, False),
        )
        for name, already_present, removed in cases:
            with self.subTest(case=name), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                variables, stubs = self.ollama_sandbox(directory, healthy=True)
                loaded = root / "loaded"
                store = root / "store" / "qwen3.5-4b"
                store.parent.mkdir()
                if already_present:
                    store.write_text("republished manifest")
                ollama = root / "fake-ollama"
                ollama.write_text(
                    "#!/bin/bash\n"
                    'echo "ollama $*" >> "$STUB_LOG"\n'
                    'case "$1" in\n'
                    f'  show) [ -e {store} ] ;;\n'
                    f'  pull) echo republished > {store} ;;\n'
                    # ollama rm talks to the service, so it needs it loaded.
                    f'  rm) [ -e {loaded} ] && rm -f {store} ;;\n'
                    "esac\n")
                ollama.chmod(0o755)
                variables["OLLAMA"] = ollama
                variables["UV"] = "uv"
                stubs["uv"] = (
                    'echo "uv $*" >> "$STUB_LOG"\n'
                    'case "$*" in\n'
                    '  *--verify-ollama-model*)\n'
                    '    echo "RuntimeError: qwen3.5:4b manifest drift" >&2\n'
                    '    exit 1 ;;\n'
                    "esac\n")
                # The launchd job (pid 77) is the only listener once loaded.
                stubs["lsof"] = f'[ -e {loaded} ] && echo 77; exit 0'
                result = run_setup_functions(
                    self.OLLAMA_FUNCTIONS + (
                        "model_state", "qwen_is_installed",
                        "qwen_matches_audit", "install_optional_models"),
                    'refresh_model_inventory() { :; }\n'
                    'install_optional_models\n',
                    variables={**variables,
                               "model_inventory": "whisper-large=present"},
                    stubs=stubs)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertIn("!! qwen3.5:4b does not match the audited model",
                              result.stdout)
                self.assertIn("qwen3.5:4b was skipped", result.stdout)
                self.assertNotIn("optional models are installed", result.stdout)
                self.assertFalse(loaded.exists(), "Ollama agent left loaded")
                self.assertFalse(variables["ollama_plist"].exists())
                self.assertFalse(variables["ollama_service_receipt"].exists())
                self.assertEqual(not store.exists(), removed, result.calls)
                # So the next setup no longer reads it as an opt-in.
                after = run_setup_functions(
                    ["cleanup_model_wanted"],
                    "cleanup_model_wanted && echo wanted || echo skipped\n",
                    variables={"WITH_ALL_MODELS": 0,
                               "ollama_plist": variables["ollama_plist"],
                               "qwen_manifest": store})
                self.assertEqual(after.stdout.strip(), "skipped")

    def test_runtime_logs_are_private_before_services_can_write_them(self):
        mac_provision = self.shell.index('provision_private_log "$dictate_log"')
        mac_ollama = self.shell.index(
            "# --- Ollama service and cleanup model")
        mac_dictation = self.shell.index('step "installing the login LaunchAgent"')
        self.assertLess(mac_provision, mac_ollama)
        self.assertLess(mac_provision, mac_dictation)
        for required in (
            'private_log_is_valid() {',
            '[ ! -L "$path" ]',
            "stat -f '%Lp'",
            'chmod 600 "$path"',
            'private_log_is_valid "$private_log"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.shell)

        windows_provision = self.powershell.index(
            'foreach ($PrivateLog in @($Log, $OllamaLog, $OllamaErrorLog)) {\n    Set-PrivateFile $PrivateLog')
        windows_ollama = self.powershell.index(
            'Start-Process -FilePath $Ollama -ArgumentList "serve"')
        self.assertLess(windows_provision, windows_ollama)
        for required in (
            'function Test-PrivateFile',
            'function Set-PrivateFile',
            'Get-ChildItem -Force -LiteralPath $Parent -ErrorAction Stop',
            'SetAccessRuleProtection($true, $false)',
            'SetOwner($CurrentSid)',
            'FileSystemRights]::FullControl',
            'Test-PrivateFile $PrivateLog',
            '$OllamaLog = Join-Path $Repo "ollama.log"',
            '$OllamaErrorLog = Join-Path $Repo "ollama-error.log"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.powershell)

    def test_install_log_records_the_run_and_the_reason_comes_first(self):
        # The log is written by rerunning setup.sh behind a plain pipeline.
        # Under the earlier `exec > >(tee ...)`, bash 3.2 could not wait for
        # the tee, so the reason arrived after Install.command's trailer.
        with tempfile.TemporaryDirectory() as directory:
            checkout = Path(directory)
            child = checkout / "setup.sh"
            child.write_text(
                "#!/bin/bash\n"
                'echo "== a step"\n'
                'echo "logged-by=${WHISPER_FACE_INSTALL_LOG:-}"\n'
                'echo "arguments=$*"\n'
                'sleep 0.2\n'
                'echo "!! the reason it stopped" >&2\n'
                "exit 3\n", encoding="utf-8")
            child.chmod(0o755)
            log = checkout / "install.log"
            for attempt in range(5):
                with self.subTest(attempt=attempt):
                    log.unlink(missing_ok=True)
                    result = run_setup_functions(
                        ["start_install_log", "run_with_install_log"],
                        'start_install_log\n'
                        'status=0\n'
                        'run_with_install_log --with-all-models '
                        '|| status=$?\n'
                        'echo "trailer status=$status"\n',
                        variables={"DIR": checkout, "install_log": log})
                    output = result.stdout
                    self.assertEqual(result.returncode, 0, result.stderr)
                    reason = output.index("!! the reason it stopped")
                    banner = output.index("!! Full log: " + str(log))
                    trailer = output.index("trailer status=3")
                    self.assertLess(reason, banner)
                    self.assertLess(banner, trailer)
                    recorded = log.read_text(encoding="utf-8")
                    self.assertIn("== a step", recorded)
                    self.assertIn("!! the reason it stopped", recorded)
                    self.assertIn(f"logged-by={log}", recorded)
                    self.assertIn("arguments=--with-all-models", recorded)
                    self.assertEqual(log.stat().st_mode & 0o777, 0o600)
                    # The self-updater reads stderr first for its rollback
                    # reason; the child's merged stderr leaves only this.
                    self.assertEqual(result.stderr.strip(),
                                     "!! the reason it stopped")

            # A reason an earlier run left in the log is never repeated.
            log.write_text("!! an earlier run's reason\n", encoding="utf-8")
            child.write_text("#!/bin/bash\necho '== a step'\nexit 4\n",
                             encoding="utf-8")
            result = run_setup_functions(
                ["start_install_log", "run_with_install_log"],
                'start_install_log\nrun_with_install_log || true\n',
                variables={"DIR": checkout, "install_log": log})
            self.assertIn("exit status 4", result.stdout)
            self.assertEqual(result.stderr, "")

    def test_a_failed_verify_reports_its_reason_where_the_app_reads_it(self):
        # verify_mac_installation shows the last line --verify prints on
        # standard output; before, that was "== verifying installation".
        verify = run_setup_functions(
            ["fail"], 'echo "== verifying installation"\n'
                      'fail "Parakeet ASR helper is missing"\n',
            variables={"VERIFY_ONLY": 1})
        self.assertEqual(verify.returncode, 1)
        self.assertEqual(verify.stdout.strip().splitlines()[-1],
                         "!! Parakeet ASR helper is missing")
        # An install keeps reasons on standard error, as before.
        install = run_setup_functions(
            ["fail"], 'fail "no room"\n', variables={"VERIFY_ONLY": 0})
        self.assertEqual(install.returncode, 1)
        self.assertEqual(install.stdout, "")
        self.assertIn("!! no room", install.stderr)

        # Every check verify_install makes names itself when it fails, so the
        # app never shows "== verifying installation" as the reason.
        checks = {
            "plutil": ("plutil", "exit 1",
                       "!! dictation LaunchAgent is not a valid property list"),
            "uv lock --check": ("uv", 'case "$*" in lock*) exit 1;; esac',
                                "!! the Python lock does not match dictate.py"),
            "uv sync --check": ("uv", 'case "$*" in sync*) exit 1;; esac',
                                "!! the locked Python environment is incomplete"),
        }
        for name, (tool, body, reason) in checks.items():
            with self.subTest(check=name), \
                    tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                helper = root / "parrot-asr-helper"
                helper.write_text("#!/bin/bash\nexit 0\n", encoding="utf-8")
                helper.chmod(0o755)
                logs = (root / "dictate.log", root / "ollama.log")
                for log in logs:
                    log.write_text("", encoding="utf-8")
                    log.chmod(0o600)
                (root / "com.berg.dictate.plist").write_text(
                    "x", encoding="utf-8")
                stubs = {"uv": "exit 0", "ffmpeg": "exit 0", "swift": "exit 0",
                         "swiftc": "exit 0", "plutil": "exit 0"}
                stubs[tool] = body
                result = run_setup_functions(
                    ["fail", "step", "private_log_is_valid",
                     "verify_cleanup_service", "verify_install"],
                    "verify_install\n",
                    variables={
                        "VERIFY_ONLY": 1, "MODE": "server", "DIR": root,
                        "parakeet_helper": helper,
                        "dictate_plist": root / "com.berg.dictate.plist",
                        "dictate_log": logs[0], "ollama_log": logs[1],
                        "ollama_plist": root / "com.berg.ollama.plist",
                    },
                    stubs=stubs)
                self.assertEqual(result.returncode, 1, result.stderr)
                self.assertTrue(
                    result.stdout.strip().splitlines()[-1].startswith(reason),
                    result.stdout)

    def test_install_command_names_the_log_only_when_this_run_wrote_it(self):
        # A copy of the shim drives a stand-in setup.sh, so nothing installs.
        for writes_log, expected in (
            (False, "Setup stopped before it could open a log"),
            (True, "The whole run, including the error above, was recorded"),
        ):
            with self.subTest(writes_log=writes_log), \
                    tempfile.TemporaryDirectory() as directory:
                folder = Path(directory)
                shim = folder / "Install.command"
                shim.write_bytes((ROOT / "Install.command").read_bytes())
                shim.chmod(0o755)
                stand_in = folder / "setup.sh"
                stand_in.write_text(
                    "#!/bin/bash\n"
                    + ('echo "this run" >> install.log\n' if writes_log
                       else "")
                    + 'echo "!! --nonsense is not an option" >&2\n'
                    "exit 2\n", encoding="utf-8")
                stand_in.chmod(0o755)
                # A log left behind by an earlier, unrelated run.
                (folder / "install.log").write_text(
                    "an earlier run\n", encoding="utf-8")
                result = subprocess.run(
                    [str(shim), "--nonsense"], cwd=folder, text=True,
                    capture_output=True, stdin=subprocess.DEVNULL,
                    timeout=30)
                self.assertEqual(result.returncode, 2)
                self.assertIn(expected, result.stdout)

    def test_an_install_waiting_for_permissions_is_not_called_complete(self):
        # On a first install macOS has granted nothing, so the service stops
        # at "Waiting for permissions" with no hotkey listener and nothing
        # verified. Printing "installation complete" and "Hold Right Option"
        # there sent people to press a key that did nothing.
        def outcome(state):
            return run_setup_functions(
                ["print_install_outcome"],
                f"skipped_models=()\nprint_install_outcome {state}\n",
                variables={"MODE": "full", "DIR": "/checkout",
                           "launcher_app": "/Apps/Whisper Face.app"})

        waiting = outcome("permissions")
        self.assertEqual(waiting.returncode, 0, waiting.stderr)
        self.assertNotIn("installation complete", waiting.stdout)
        self.assertNotIn("release to paste", waiting.stdout)
        self.assertIn("Not finished yet", waiting.stdout)
        for permission in ("Input Monitoring", "Accessibility", "Microphone"):
            with self.subTest(permission=permission):
                self.assertIn(permission, waiting.stdout)
        self.assertIn("./setup.sh --verify", waiting.stdout)

        ready = outcome("ready")
        self.assertEqual(ready.returncode, 0, ready.stderr)
        self.assertIn("== installation complete", ready.stdout)
        self.assertIn("Hold Right Option, speak, and release to paste.",
                      ready.stdout)

    def test_installers_fail_before_side_effects_for_read_only_checkouts(self):
        mac_call = self.shell.index("\ncheck_install_location\n")
        mac_homebrew = self.shell.index("installing Homebrew")
        mac_ollama = self.shell.index(
            "# --- Ollama service and cleanup model")
        self.assertLess(mac_call, mac_homebrew)
        self.assertLess(mac_call, mac_ollama)
        for required in (
            'mktemp "$DIR/.whisper-face-write.XXXXXXXX"',
            "umask 077",
            "trap 'rm -f -- \"$proof\"' EXIT",
            'checkout is not writable; copy or extract Whisper Face',
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.shell)

        windows_probe = self.powershell.index("function Confirm-WritableCheckout")
        windows_call = self.powershell.index("Confirm-WritableCheckout", windows_probe + 1)
        windows_winget = self.powershell.index('Write-Step "installing uv"')
        windows_ollama = self.powershell.index(
            'Write-Step "starting the local Ollama service"')
        self.assertLess(windows_call, windows_winget)
        self.assertLess(windows_call, windows_ollama)
        for required in (
            '[Guid]::NewGuid().ToString("N")',
            '[IO.FileMode]::CreateNew',
            'Set-PrivateFile $Proof',
            'Remove-Item -LiteralPath $Proof -Force -ErrorAction Stop',
            'checkout is not writable; copy or extract Whisper Face',
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.powershell)

    def test_running_from_the_disk_image_says_so_instead_of_disk_space(self):
        # A mounted image has a few megabytes free, so checking space first
        # told people with hundreds of gigabytes free that they needed 5 GB.
        functions = ["fail", "checkout_volume_is_read_only",
                     "confirm_writable_checkout", "check_install_location"]
        image = "/Volumes/Whisper Face 1.2.3"
        df = ("cat <<'DF'\n"
              "Filesystem 1024-blocks Used Available Capacity Mounted on\n"
              f"/dev/disk9s1 38176 25416 12128 68% {image}\n"
              "DF")
        cases = (
            ("read-only image", True, f"/dev/disk9s1 on {image} "
             "(apfs, local, nodev, nosuid, read-only, noowners)",
             "running from the disk image, which is read-only"),
            ("read-only folder elsewhere", True, "/dev/disk3s5 on "
             "/System/Volumes/Data (apfs, local, journaled)",
             "checkout is not writable"),
            ("writable but full", False, "/dev/disk3s5 on "
             "/System/Volumes/Data (apfs, local, journaled)",
             "at least 5 GB of free disk space is required"),
        )
        for name, read_only, mounts, expected in cases:
            with self.subTest(case=name), \
                    tempfile.TemporaryDirectory() as directory:
                checkout = Path(directory) / "Whisper Face 1.2.3"
                checkout.mkdir()
                if read_only:
                    checkout.chmod(0o555)
                try:
                    result = run_setup_functions(
                        functions, "check_install_location\n",
                        variables={"DIR": checkout, "VERIFY_ONLY": 0,
                                   "required_kb": 5242880, "required_gb": 5},
                        stubs={"df": df, "mount": f"echo '{mounts}'"})
                finally:
                    checkout.chmod(0o755)
                self.assertEqual(result.returncode, 1)
                self.assertIn(expected, result.stderr)
                if read_only:
                    self.assertNotIn("free disk space", result.stderr)

    def test_missing_build_tools_and_old_macos_fail_before_any_download(self):
        # /usr/bin/swift and swiftc are shims on every Mac, so the old
        # `command -v` check could not fail; a missing toolchain surfaced
        # only when `swift build` ran, after the models had downloaded.
        swift_six = ('[ "$1" = "--find" ] && { echo /usr/bin/swiftc; exit 0; }\n'
                     'echo "Apple Swift version 6.1.2 (swiftlang-6.1.2)"')
        cases = (
            ("no Command Line Tools", "exit 2", swift_six, "14.6",
             "Command Line Tools are not installed"),
            ("Swift 5 toolchain", "echo /Library/Developer/CommandLineTools",
             '[ "$1" = "--find" ] && exit 0\n'
             'echo "Apple Swift version 5.10 (swiftlang-5.10)"', "14.6",
             "Swift 6 or newer is required"),
            ("macOS 13", "echo /Library/Developer/CommandLineTools",
             swift_six, "13.6.1", "macOS 14 (Sonoma) or newer is required"),
        )
        for name, xcode_select, xcrun, macos, expected in cases:
            with self.subTest(case=name):
                result = run_setup_functions(
                    ["fail", "require_supported_macos", "require_build_tools"],
                    "require_supported_macos\nrequire_build_tools\n"
                    'echo "preflight passed"\n',
                    variables={"VERIFY_ONLY": 0},
                    stubs={"xcode-select": xcode_select, "xcrun": xcrun,
                           "sw_vers": f"echo {macos}"})
                self.assertEqual(result.returncode, 1, result.stdout)
                self.assertIn(expected, result.stderr)
                self.assertNotIn("preflight passed", result.stdout)
        passing = run_setup_functions(
            ["fail", "require_supported_macos", "require_build_tools"],
            'require_supported_macos\nrequire_build_tools\necho ok\n',
            variables={"VERIFY_ONLY": 0},
            stubs={"xcode-select": "echo /Library/Developer/CommandLineTools",
                   "xcrun": swift_six, "sw_vers": "echo 15.4"})
        self.assertEqual(passing.returncode, 0, passing.stderr)

    def test_mac_installer_adopts_an_installed_homebrew_missing_from_path(self):
        # The self-updater runs this installer from a detached launchd job, and
        # launchd hands such a job the minimal PATH rather than the login one.
        # Concluding Homebrew is absent there sent setup into a TTY-less,
        # sudo-less reinstall that aborted in about a second; under `set -e`
        # that killed the update before the existing brew could be adopted.
        adopt = self.shell.index("adopt_installed_homebrew() {")
        probe = self.shell.index(
            "command -v brew >/dev/null 2>&1 || adopt_installed_homebrew")
        bootstrap = self.shell.index(
            'step "installing Homebrew (its official installer may request')
        self.assertLess(adopt, probe)
        self.assertLess(probe, bootstrap)
        for required in (
            "for candidate in /opt/homebrew/bin/brew /usr/local/bin/brew; do",
            'eval "$("$candidate" shellenv)"',
            # A failed bootstrap must name itself, not die silently under -e.
            '|| fail "the Homebrew installer did not complete',
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.shell)
        # The detached updater must carry a usable PATH across launchctl.
        self.assertIn('"/usr/bin/env", f"PATH=', self.script)

    def test_mac_installer_does_not_trust_the_inherited_path(self):
        # A self-update runs this installer from a detached launchd job, which
        # inherits either launchd's minimal PATH or whatever the caller handed
        # over. The dictation agent's own PATH carries Homebrew but not
        # /usr/sbin, where lsof lives. Losing lsof made ollama_listener_pid
        # report "no unique listener" -- indistinguishable from an unhealthy
        # service -- so the readiness wait spun until it timed out and the
        # update rolled back with a misleading reason.
        normalise = self.shell.index(
            'for system_path in /usr/bin /bin /usr/sbin /sbin; do')
        self.assertIn('export PATH', self.shell)
        # Normalising must precede every tool lookup that depends on it.
        for dependent in (
            'ollama_listener_pid() {',
            'command -v lsof >/dev/null 2>&1',
            'command -v brew >/dev/null 2>&1',
        ):
            with self.subTest(dependent=dependent):
                self.assertLess(normalise, self.shell.index(dependent))
        # An absent tool and an unhealthy service must not share a symptom.
        guard = self.shell.index('command -v lsof >/dev/null 2>&1')
        self.assertIn('lsof was not found on PATH', self.shell)
        self.assertLess(guard, self.shell.index(
            'step "configuring the tuned local Ollama service"'))

    def test_mac_ollama_identity_helper_fails_closed(self):
        helper_start = self.shell.index("ollama_listener_pid()")
        helper_end = self.shell.index("reload_agent()", helper_start)
        helpers = self.shell[helper_start:helper_end]
        for required in (
            "*$'\\n'*) return 1",
            '[ "${#digest}" -eq 64 ]',
            '*[!0-9a-f]*) return 1',
            '[ -f "$receipt" ] || return 1',
            'wc -c < "$receipt"',
            '[ "$receipt_size" = "65" ]',
            'valid_sha256_digest "$receipt_digest"',
            '[ "$receipt_digest" = "$desired_digest" ]',
            'cmp -s "$desired_plist" "$installed_plist"',
            'agent_is_running com.berg.ollama',
            '[ "$running_pid" = "$listening_pid" ]',
            'http://127.0.0.1:11434/api/tags',
        ):
            with self.subTest(required=required):
                self.assertIn(required, helpers)

    def test_mac_verify_reconstructs_identity_without_mutating_install(self):
        verify = (shell_function("verify_cleanup_service")
                  + shell_function("verify_install"))
        for required in (
            'render_plist "$DIR/com.berg.ollama.plist.template"',
            "printf -v verify_cleanup 'rm -f -- %q'",
            'trap "$verify_cleanup" EXIT',
            'shasum -a 256 "$verify_ollama_plist"',
            'ollama_service_identity_is_valid "$verify_ollama_plist"',
            '"$ollama_plist" "$ollama_service_receipt"',
            'fail "Ollama LaunchAgent configuration or process identity is stale"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, verify)
        self.assertGreaterEqual(verify.count("trap - EXIT"), 2)
        for forbidden in (
            "reload_agent",
            "launchctl bootstrap",
            "launchctl kickstart",
            'mv -f "$verify_ollama_plist" "$ollama_plist"',
            'mv -f "$receipt_temporary"',
            'services stop ollama',
        ):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, verify)

    def test_both_installers_use_current_runtime_and_preload_contract(self):
        for name, installer in (
            ("shell", self.shell), ("powershell", self.powershell)
        ):
            with self.subTest(installer=name):
                self.assertIn("dictate.py", installer)
                self.assertIn("parrot_core.py", installer)
                self.assertIn("voice_compiler.py", installer)
                self.assertIn("process_verifier.py", installer)
                self.assertIn("prewarmed_verifier.py", installer)
                self.assertIn("whisper_verifier_adapter.py", installer)
                self.assertIn("relisten_activation.py", installer)
                self.assertIn("insertion_integrity.py", installer)
                self.assertIn("personal_regression.py", installer)
                self.assertIn("shadow_candidate_gate.py", installer)
                self.assertIn("cleanup_circuit_breaker.py", installer)
                self.assertIn("delayed_cleanup_activation.py", installer)
                self.assertIn("delayed_cleanup_merge.py", installer)
                self.assertIn(
                    "macos_delayed_cleanup_destination.py", installer)
                self.assertIn("model_wallet.py", installer)
                self.assertIn("model_wallet_shadow.py", installer)
                self.assertIn("model_readiness_evidence.py", installer)
                self.assertIn("acoustic_keyword_activation.py", installer)
                self.assertIn(
                    "benchmark_acoustic_keyword_activation.py", installer)
                self.assertIn("acoustic_calibration.py", installer)
                self.assertIn(
                    "acoustic_calibration_activation.py", installer)
                self.assertIn(
                    "benchmark_acoustic_calibration_activation.py", installer)
                self.assertIn("acoustic_time_machine.py", installer)
                self.assertIn("risky_action_confirmation.py", installer)
                self.assertIn("point_and_speak_resolver.py", installer)
                self.assertIn("point_and_speak_transaction.py", installer)
                self.assertIn("macos_point_and_speak_snapshot.py", installer)
                self.assertIn("macos_drop_to_target_snapshot.py", installer)
                self.assertIn("drop_to_target.py", installer)
                self.assertIn("whisper_face_gui.py", installer)
                self.assertIn("whisper_face_theme.py", installer)
                self.assertIn("support_bundle.py", installer)
                self.assertIn("side_by_side_update.py", installer)
                self.assertIn("safe_update_advisor.py", installer)
                self.assertIn("release_manifest.py", installer)
                self.assertIn("dictate.py.lock", installer)
                self.assertIn("--preload-models", installer)
                self.assertIn("--verify", installer)
                self.assertIn("--verify-ollama-model", installer)

    def test_windows_precreates_private_runtime_logs_with_verified_acl(self):
        provision = self.powershell.index(
            'foreach ($PrivateLog in @($Log, $OllamaLog, $OllamaErrorLog)) {\n'
            '    Set-PrivateFile $PrivateLog')
        start = self.powershell.index(
            'Start-Process -FilePath $Ollama -ArgumentList "serve"')
        self.assertLess(provision, start)
        for required in (
            'New-Item -ItemType File -Path $Path -Force',
            'Get-ChildItem -Force -LiteralPath $Parent -ErrorAction Stop',
            'SetAccessRuleProtection($true, $false)',
            'SetOwner($CurrentSid)',
            'throw "could not apply a private runtime log ACL: $Path"',
            'if (-not (Test-PrivateFile $PrivateLog))',
            '$OllamaLog = Join-Path $Repo "ollama.log"',
            '$OllamaErrorLog = Join-Path $Repo "ollama-error.log"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, self.powershell)

    def test_windows_runs_every_native_command_through_the_exit_code_wrappers(self):
        # Static, because no PowerShell runs here: under Windows PowerShell
        # 5.1 with $ErrorActionPreference = "Stop", a native command whose
        # stderr is redirected ends the install on its first line of output.
        # `ollama show` for an absent model did exactly that on every first
        # install. Only the two wrappers relax the preference and read the
        # exit code, so a native call anywhere else is a defect.
        lines = self.powershell.splitlines()
        wrappers = set()
        in_launcher = False
        for start, line in enumerate(lines):
            if line.startswith(("function Invoke-Native(",
                                "function Invoke-NativeStep(")):
                end = next(index for index in range(start, len(lines))
                           if lines[index] == "}")
                wrappers.update(range(start, end + 1))
        self.assertGreater(len(wrappers), 10)
        native_call = re.compile(r"(^|[\s(=])&\s+[$'\"\w]")
        redirection = re.compile(r"(\*|\b2)>")
        for number, line in enumerate(lines):
            # The generated launcher is text written to launch.ps1, which runs
            # without "Stop"; it is not executed by this script.
            if line.endswith('@"'):
                in_launcher = True
            elif line.startswith('"@'):
                in_launcher = False
            code = line.split("#", 1)[0]
            if in_launcher or number in wrappers:
                continue
            with self.subTest(line=number + 1, text=line.strip()):
                self.assertIsNone(native_call.search(code))
                unquoted = re.sub(r"'[^']*'|\"[^\"]*\"", "''", code)
                self.assertIsNone(redirection.search(unquoted))
        self.assertIn('if ((Invoke-Native $Ollama @("show", "qwen3.5:4b"))',
                      self.powershell)
        # A command that cannot start sets no exit code, so each wrapper
        # clears it first rather than returning the previous command's 0.
        for start in sorted(wrappers):
            line = lines[start]
            if not line.startswith("function Invoke-Native"):
                continue
            body = lines[start:next(index for index in range(start, len(lines))
                                    if lines[index] == "}")]
            reset = next(index for index, text in enumerate(body)
                         if text.strip() == "$global:LASTEXITCODE = -1")
            call = next(index for index, text in enumerate(body)
                        if native_call.search(text.split("#", 1)[0]))
            with self.subTest(wrapper=line):
                self.assertLess(reset, call)

        # The failure trap covers the whole script, argument errors included,
        # and strict mode throws on an unset variable inside it, which would
        # replace the reason. Everything it reads is set before any throw.
        trap = self.powershell[self.powershell.index("\ntrap {"):]
        trap = trap[:trap.index("\n}\n")]
        first_throw = self.powershell.index("throw ")
        for variable in sorted(set(re.findall(r"\$script:\w+", trap))):
            with self.subTest(trap_reads=variable):
                self.assertLess(
                    self.powershell.index(f"{variable} = "), first_throw)

    def test_windows_login_task_keeps_running_on_battery_and_restarts(self):
        # Static, because no PowerShell runs here. Task Scheduler defaults
        # stop a task when a laptop is unplugged, never start it on battery,
        # and run it at below-normal priority; and a launcher that drops
        # uv's exit code makes every crash look like success, so the restart
        # policy never fires.
        settings_start = self.powershell.index(
            "$Settings = New-ScheduledTaskSettingsSet")
        settings = self.powershell[settings_start:self.powershell.index(
            "Register-ScheduledTask", settings_start)]
        for option in ("-AllowStartIfOnBatteries",
                       "-DontStopIfGoingOnBatteries", "-Priority 4",
                       "-RestartCount 3"):
            with self.subTest(option=option):
                self.assertIn(option, settings)
        launcher = self.powershell[
            self.powershell.index('$LauncherBody = @"'):]
        launcher = launcher[:launcher.index('"@')]
        lines = [line for line in launcher.splitlines() if line.strip()]
        self.assertEqual(lines[-1], "exit `$LASTEXITCODE")
        self.assertIn('`$env:PYTHONUTF8 = "1"', launcher)

    def test_windows_verify_binds_login_task_to_its_current_checkout(self):
        helper_start = self.powershell.index("function Confirm-TaskLauncherBinding")
        helper_end = self.powershell.index("function Confirm-Installation", helper_start)
        helper = self.powershell[helper_start:helper_end]
        self.assertIn(
            '$LauncherReceipt = Join-Path $LauncherDir "launch.sha256"',
            self.powershell)
        self.assertIn(
            "Get-FileHash -Algorithm SHA256 -LiteralPath $Path",
            self.powershell)
        for required in (
            "[IO.File]::ReadAllText($LauncherReceipt)",
            "Windows login launcher has changed; rerun Install.cmd",
            "Windows login launcher is outside this checkout; rerun Install.cmd",
            "Set-Location '$EscapedRepo'",
            "run --locked --script '$EscapedScript'",
            "*>> '$EscapedLog'",
            "[Security.Principal.WindowsIdentity]::GetCurrent()",
            "$CurrentIdentity.Name",
            "$CurrentIdentity.User.Value",
            "$Task.Principal.UserId -ieq $CurrentPrincipalId",
            "if (-not $TaskPrincipalMatches)",
            "$Actions.Count -ne 1",
            '$Actions[0].Execute -ine "powershell.exe"',
            '$Actions[0].Arguments -cne $ExpectedArguments',
            "Windows login task does not launch this checkout; rerun Install.cmd",
        ):
            with self.subTest(required=required):
                self.assertIn(required, helper)

        verify_start = self.powershell.index("function Confirm-Installation")
        verify_end = self.powershell.index("if ($VerifyOnly)", verify_start)
        verify = self.powershell[verify_start:verify_end]
        self.assertIn("Confirm-TaskLauncherBinding", verify)

        task_start = self.powershell.index(
            'Write-Step "installing the Windows login task"')
        task = self.powershell[task_start:]
        for required in (
            "$LauncherDigest = Get-LauncherDigest $Launcher",
            "Set-Content -Path $LauncherReceipt -Value $LauncherDigest",
            "Set-PrivateFile $LauncherReceipt",
        ):
            with self.subTest(required=required):
                self.assertIn(required, task)

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
        self.assertIn("run_native_hud_smoke_test", self.script)
        self.assertIn("CASpringAnimation", self.script)
        self.assertIn("if not IS_MACOS", self.script)

    def test_whisper_face_assets_and_preference_ship_on_both_platforms(self):
        template = (ROOT / "preferences.template.json").read_text(
            encoding="utf-8")
        self.assertIn('"face": "parrot"', template)
        self.assertIn('"acoustic_time_machine": false', template)
        self.assertIn('"voice_object_commands": false', template)
        self.assertIn('"hotkey": "alt_r"', template)
        self.assertIn('"sounds": "system"', template)
        self.assertIn('"recent_dictations": false', template)
        # The dictation language ships defaulted to English, so an existing
        # install keeps the fast native recognizer without touching anything.
        self.assertIn('"language": "en"', template)
        self.assertIn("whisper_face_theme.py", self.shell)
        self.assertIn("whisper_face_theme.py", self.powershell)
        self.assertIn("whisper_face_characters.py", self.shell)
        self.assertIn("whisper_face_characters.py", self.powershell)
        self.assertIn("whisper_face_render.py", self.shell)
        self.assertIn("whisper_face_render.py", self.powershell)
        for face in ("parrot", "fox", "bear", "owl", "cat", "dog", "wolf",
                     "pig", "panda", "tiger", "frog", "rabbit", "hedgehog",
                     "penguin", "pickles", "olive"):
            # Every menu-bar frame ships, including the blink frame the
            # status bar flashes at rest.
            for frame in ("idle", "talk", "blink"):
                self.assertIn(f"icons/faces/{face}-{frame}.svg", self.shell)
                self.assertIn(
                    f"icons\\faces\\{face}-{frame}.svg", self.powershell)
        for runtime_module in (
                "voice_objects.py", "voice_object_command_parser.py",
                "voice_inbox.py", "voice_object_inbox_bridge.py",
                "macos_email_compose.py", "macos_voice_draft_clipboard.py",
                "demonstration_drafts.py"):
            with self.subTest(runtime_module=runtime_module):
                self.assertIn(runtime_module, self.shell)
                self.assertIn(runtime_module, self.powershell)
        # The first-party feedback cues are committed assets, so both
        # installers have to notice when a checkout is missing one.
        for cue in ("start", "finish", "review", "error"):
            relative = f"sounds/{cue}.wav"
            with self.subTest(relative=relative):
                self.assertTrue((ROOT / relative).is_file())
                self.assertIn(relative, self.shell)
                self.assertIn(relative.replace("/", "\\"), self.powershell)
        self.assertIn("voice_inbox.json", self.shell)
        self.assertIn("voice_inbox.json", self.powershell)
        self.assertIn("demonstrations.json", self.shell)
        self.assertIn("demonstrations.json", self.powershell)
        self.assertIn("delayed_cleanup_activation.json", self.shell)
        self.assertIn("delayed_cleanup_activation.json", self.powershell)
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("voice_inbox.json", gitignore)
        self.assertIn(".voice_inbox.json.*.tmp", gitignore)
        self.assertIn("demonstrations.json", gitignore)
        self.assertIn(".demonstrations.json.*.tmp", gitignore)
        self.assertIn("delayed_cleanup_activation.json", gitignore)
        self.assertIn(".delayed_cleanup_activation.json.*.tmp", gitignore)
        for face in ("parrot", "fox", "owl", "cat", "bear",
                     "dog", "wolf", "pig", "panda", "tiger",
                     "frog", "rabbit", "hedgehog", "penguin",
                     "pickles", "olive"):
            # The flat silhouette drives the menu bar with two frames; the
            # colored character adds the mid-syllable half frame the site's
            # flap animation passes through.
            for directory, frames in (
                    ("icons/faces", ("idle", "talk")),
                    ("icons/faces/color", ("idle", "half", "talk"))):
                for frame in frames:
                    relative = f"{directory}/{face}-{frame}.svg"
                    with self.subTest(relative=relative):
                        self.assertTrue((ROOT / relative).exists())
                        self.assertIn(relative, self.shell)
                        self.assertIn(
                            relative.replace("/", "\\"), self.powershell)
        for expected in (
            'APP_NAME = "Whisper Face"',
            "FACE_CHOICES = (",
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

    def test_mac_installs_generic_launcher_with_external_checkout_receipt(self):
        launcher_tool = (
            ROOT / "scripts" / "macos_launcher_app.py"
        ).read_text(encoding="utf-8")
        gui_source = (ROOT / "whisper_face_gui.py").read_text(encoding="utf-8")
        for expected in (
            "scripts/macos_launcher_app.py",
            "config/macos-signing-policy.json",
            'launcher_app="$HOME/Applications/Whisper Face.app"',
            'macos_launcher_app.py" "${launcher_install_args[@]}"',
            'macos_launcher_app.py" verify',
            '--checkout "$DIR"',
            '--receipt "$launcher_receipt"',
            '--source-app "$packaged_launcher_app"',
            "--installed-runtime",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, self.shell)
        self.assertNotIn("macos_launcher_app.py", self.powershell)
        self.assertIn('"CFBundlePackageType": "APPL"', launcher_tool)
        # The installed app carries the brand icon, so macOS has artwork to
        # show beside "Whisper Face" in the microphone and privacy panes.
        self.assertIn('"CFBundleIconFile": ICON_NAME', launcher_tool)
        self.assertIn(
            "_atomic_write(app / ICON_FILE, _icon_bytes(), 0o644)", launcher_tool)
        self.assertTrue((ROOT / "icons" / "WhisperFace.icns").is_file())
        self.assertIn("import AppKit", launcher_tool)
        self.assertIn('"-framework", "AppKit"', launcher_tool)
        self.assertIn('shutil.which("swiftc")', launcher_tool)
        self.assertIn("func requestExistingGUI(at socketPath: String)", launcher_tool)
        self.assertIn("requestExistingGUI(at:", launcher_tool)
        self.assertIn('process.arguments = ["-U", socketPath]', launcher_tool)
        self.assertIn('isExecutableFile(atPath: "/usr/bin/nc")', launcher_tool)
        self.assertIn("addingTimeInterval(5.0)", launcher_tool)
        self.assertIn("launcher must not embed runtime source", launcher_tool)
        self.assertIn("launcher-install.json", launcher_tool)
        self.assertIn("macos-signing-policy.json", launcher_tool)
        self.assertIn("0o600", launcher_tool)
        # The local install path ad-hoc signs the assembled bundle so macOS
        # records a valid Designated Requirement, then verifies that signature
        # and refuses an ad-hoc bundle where a release signature is required.
        self.assertIn('"--force", "--sign", "-", "--identifier"', launcher_tool)
        self.assertIn("Contents/_CodeSignature/CodeResources", launcher_tool)
        self.assertIn('"--display", "--verbose=2"', launcher_tool)
        self.assertIn("release launcher must be signed", launcher_tool)
        self.assertNotIn("dictate.py\n", launcher_tool)
        self.assertNotIn('"/usr/bin/python', launcher_tool)
        self.assertNotIn('"uv"', launcher_tool)
        self.assertIn("command -v swiftc", self.shell)
        self.assertIn("self.window.makeKeyAndOrderFront_(None)", gui_source)
        self.assertIn("activateIgnoringOtherApps_(True)", gui_source)
        self.assertIn(
            'start_gui_activation_server(STATUS["bar"].gui)', self.script)
        self.assertLess(
            self.script.index(
                'start_gui_activation_server(STATUS["bar"].gui)'),
            self.script.index("if not ensure_event_permissions():"),
        )
        self.assertIn(
            'name="whisper-face-permission-recheck"', self.script)

    def test_update_and_rollback_guide_uses_supported_install_paths(self):
        guide = (
            ROOT / "docs" / "distribution" / "update-and-rollback.md"
        ).read_text(encoding="utf-8")
        for expected in (
            "Install.command",
            "Install.cmd",
            "./setup.sh --verify",
            ".\\setup.ps1 --verify",
            "git switch --detach <known-good-commit>",
            "snippets.json",
            "tones.json",
            "preferences.json",
            "acoustic_keyword_memory.json",
            "acoustic_keyword_activation.json",
            "acoustic_calibration_activation.json",
            "relisten_activation.json",
            "delayed_cleanup_activation.json",
            "dictionary.txt",
            "transcripts.jsonl",
            "learned.json",
            "voice_inbox.json",
            "demonstrations.json",
        ):
            with self.subTest(expected=expected):
                self.assertIn(expected, guide)
        self.assertIn("Never use `git reset --hard`", guide)

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

    def test_every_uv_call_runs_on_one_python_the_lock_supports(self):
        # uv otherwise picks the newest CPython it can find or download, and
        # the lock has no mlx, torch, ctranslate2, or onnxruntime wheels for
        # 3.15; every fresh install would stop at its first uv call.
        def satisfies(version, specifier):
            for clause in specifier.replace(" ", "").split(","):
                operator, bound = re.match(r"(>=|<=|<|>|==)(.+)", clause).groups()
                left = tuple(int(part) for part in version.split("."))
                right = tuple(int(part) for part in bound.split("."))
                width = max(len(left), len(right))
                left += (0,) * (width - len(left))
                right += (0,) * (width - len(right))
                if not {">=": left >= right, "<=": left <= right,
                        "<": left < right, ">": left > right,
                        "==": left == right}[operator]:
                    return False
            return True

        declared = re.search(
            r'(?m)^# requires-python = "([^"]+)"$', self.script).group(1)
        locked = re.search(
            r'(?m)^requires-python = "([^"]+)"$', self.lock).group(1)
        self.assertEqual(declared.replace(" ", ""), locked.replace(" ", ""))
        self.assertFalse(satisfies("3.15", declared))
        self.assertTrue(satisfies("3.14", declared))

        mac = re.search(r'(?m)^PYTHON_VERSION="([0-9.]+)"$', self.shell)
        windows = re.search(r'(?m)^\$PythonVersion = "([0-9.]+)"$',
                            self.powershell)
        self.assertIsNotNone(mac)
        self.assertIsNotNone(windows)
        self.assertEqual(mac.group(1), windows.group(1))
        self.assertTrue(satisfies(mac.group(1), declared))
        self.assertIn('export UV_PYTHON="$PYTHON_VERSION"', self.shell)
        self.assertIn("$env:UV_PYTHON = $PythonVersion", self.powershell)

        # The login service must ask for the same interpreter, or it builds
        # a second environment on whatever Python is first on its PATH.
        template = (ROOT / "com.berg.dictate.plist.template").read_text(
            encoding="utf-8")
        rendered = re.sub(r"(?m)^.*__(LAUNCHER_PREFIX|EXTRA_ARGS)__.*\n", "",
                          template)
        rendered = (rendered.replace("__UV__", "/opt/homebrew/bin/uv")
                    .replace("__DIR__", "/checkout")
                    .replace("__PYTHON__", mac.group(1)))
        service = plistlib.loads(rendered.encode("utf-8"))
        self.assertEqual(service["EnvironmentVariables"]["UV_PYTHON"],
                         mac.group(1))
        self.assertIn('-e "s|__PYTHON__|$PYTHON_VERSION|g"', self.shell)
        self.assertIn('`$env:UV_PYTHON = "$PythonVersion"', self.powershell)

    def test_windows_runtime_uses_equivalent_models_and_native_service(self):
        self.assertIn('else "turbo"', self.script)
        self.assertIn('else "tiny"', self.script)
        self.assertIn("get_cuda_device_count", self.script)
        self.assertIn('options.append(("cpu", "int8"))', self.script)
        self.assertIn("WindowsStatusBar", self.script)
        self.assertIn("snapshot_windows_clipboard", self.script)


if __name__ == "__main__":
    unittest.main()
