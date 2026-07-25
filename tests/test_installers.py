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
            "uv run tests/test_acoustic_time_machine.py",
            "uv run tests/test_acoustic_calibration.py",
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
            "uv run tests/test_voice_inbox.py",
            "uv run tests/test_voice_object_inbox_bridge.py",
            "uv run tests/test_risky_action_confirmation.py",
            "uv run tests/test_demonstration_drafts.py",
            "uv run tests/test_competitor_benchmark.py",
            "uv run tests/test_public_scorecard.py",
            "uv run tests/test_personal_regression.py",
            "uv run tests/test_support_bundle.py",
            "uv run tests/test_whisper_face_gui.py",
            "uv run --locked --script dictate.py --native-gui-smoke-test",
            "uv run tests/test_installers.py",
            "uv run tests/test_macos_distribution.py",
            "uv run tests/test_safe_update_advisor.py",
            "uv run tests/test_side_by_side_update.py",
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
        ):
            with self.subTest(batch_gate=batch_gate):
                self.assertIn(batch_gate, pull_request)
                self.assertIn(batch_gate, macos_workflow)
                self.assertIn(batch_gate, windows_workflow)

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
        verify_start = self.shell.index("verify_install() {")
        verify_end = self.shell.index('if [ "$VERIFY_ONLY" -eq 1 ]')
        verify = self.shell[verify_start:verify_end]
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
        self.assertIn('fail "Qwen model manifest verification failed"', verify)
        install = self.shell[self.shell.index(
            'step "installing the locked Python environment"'):]
        install_manifest = install.index("--verify-ollama-model")
        self.assertLess(
            install.rindex('if [ "$qwen_installed" -eq 1 ]; then',
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
        configuration_start = self.shell.index(
            'step "configuring the tuned local Ollama service"')
        branch_start = self.shell.index(
            'if ollama_service_identity_is_valid ', configuration_start)
        branch_end = self.shell.index('echo -n "== waiting for Ollama"')
        branch = self.shell[branch_start:branch_end]
        preamble = self.shell[configuration_start:branch_start]
        self.assertIn(
            'render_plist com.berg.ollama.plist.template '
            '"$desired_ollama_plist"', preamble)
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
        stop = branch.index('"$BREW" services stop ollama')
        reload = branch.index('reload_agent com.berg.ollama "$ollama_plist"')
        self.assertLess(reuse, stop)
        self.assertLess(stop, reload)
        self.assertNotIn(
            '"$BREW" services stop ollama', self.shell[:branch_start])
        postamble = self.shell[branch_end:]
        self.assertIn('ollama-service.sha256', self.shell)
        for required in (
            'install -d -m 700 "$service_receipt_dir"',
            'chmod 600 "$receipt_temporary"',
            'mv -f "$receipt_temporary" "$ollama_service_receipt"',
            'fail "Ollama service identity could not be verified"',
        ):
            with self.subTest(required=required):
                self.assertIn(required, postamble)

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

    def test_runtime_logs_are_private_before_services_can_write_them(self):
        mac_provision = self.shell.index('provision_private_log "$dictate_log"')
        mac_ollama = self.shell.index(
            'step "configuring the tuned local Ollama service"')
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

    def test_installers_fail_before_side_effects_for_read_only_checkouts(self):
        mac_probe = self.shell.index("confirm_writable_checkout()")
        mac_call = self.shell.index("confirm_writable_checkout\n", mac_probe)
        mac_homebrew = self.shell.index("installing Homebrew")
        mac_ollama = self.shell.index(
            'step "configuring the tuned local Ollama service"')
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
        verify_start = self.shell.index("verify_install()")
        verify_end = self.shell.index(
            'if [ "$VERIFY_ONLY" -eq 1 ]', verify_start)
        verify = self.shell[verify_start:verify_end]
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
                self.assertIn("insertion_integrity.py", installer)
                self.assertIn("personal_regression.py", installer)
                self.assertIn("cleanup_circuit_breaker.py", installer)
                self.assertIn("model_wallet.py", installer)
                self.assertIn("model_wallet_shadow.py", installer)
                self.assertIn("model_readiness_evidence.py", installer)
                self.assertIn("acoustic_time_machine.py", installer)
                self.assertIn("risky_action_confirmation.py", installer)
                self.assertIn("point_and_speak_resolver.py", installer)
                self.assertIn("point_and_speak_transaction.py", installer)
                self.assertIn("macos_point_and_speak_snapshot.py", installer)
                self.assertIn("macos_drop_to_target_snapshot.py", installer)
                self.assertIn("drop_to_target.py", installer)
                self.assertIn("whisper_face_gui.py", installer)
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
            '& icacls $LauncherReceipt /inheritance:r /grant:r '
            '"${env:USERNAME}:(F)" /Q',
            'throw "could not apply the private Windows launcher receipt ACL"',
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
        self.assertIn("if not IS_MACOS", self.script)

    def test_whisper_face_assets_and_preference_ship_on_both_platforms(self):
        template = (ROOT / "preferences.template.json").read_text(
            encoding="utf-8")
        self.assertIn('"face": "parrot"', template)
        self.assertIn('"acoustic_time_machine": false', template)
        self.assertIn('"voice_object_commands": false', template)
        for runtime_module in (
                "voice_objects.py", "voice_object_command_parser.py",
                "voice_inbox.py", "voice_object_inbox_bridge.py",
                "macos_email_compose.py", "macos_voice_draft_clipboard.py",
                "demonstration_drafts.py"):
            with self.subTest(runtime_module=runtime_module):
                self.assertIn(runtime_module, self.shell)
                self.assertIn(runtime_module, self.powershell)
        self.assertIn("voice_inbox.json", self.shell)
        self.assertIn("voice_inbox.json", self.powershell)
        self.assertIn("demonstrations.json", self.shell)
        self.assertIn("demonstrations.json", self.powershell)
        gitignore = (ROOT / ".gitignore").read_text(encoding="utf-8")
        self.assertIn("voice_inbox.json", gitignore)
        self.assertIn(".voice_inbox.json.*.tmp", gitignore)
        self.assertIn("demonstrations.json", gitignore)
        self.assertIn(".demonstrations.json.*.tmp", gitignore)
        for face in ("parrot", "fox", "owl", "cat", "bear",
                     "dog", "wolf", "pig", "panda", "tiger"):
            for frame in ("idle", "talk"):
                relative = f"icons/faces/{face}-{frame}.svg"
                with self.subTest(relative=relative):
                    self.assertTrue((ROOT / relative).exists())
                    self.assertIn(relative, self.shell)
                    self.assertIn(relative.replace("/", "\\"), self.powershell)
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

    def test_windows_runtime_uses_equivalent_models_and_native_service(self):
        self.assertIn('else "turbo"', self.script)
        self.assertIn('else "tiny"', self.script)
        self.assertIn("get_cuda_device_count", self.script)
        self.assertIn('options.append(("cpu", "int8"))', self.script)
        self.assertIn("WindowsStatusBar", self.script)
        self.assertIn("snapshot_windows_clipboard", self.script)


if __name__ == "__main__":
    unittest.main()
