# /// script
# requires-python = ">=3.10"
# dependencies = ["numpy"]
# ///
"""Whole-program tests: the real dictate.py, booted on a fake Mac.

tests/test_dictate.py compiles selected definitions against hand-written
stubs, so a wiring error between them, or in main(), passes it. Here the real
module is imported, main() runs as it does at login on a fresh install, and
each dictation goes from a key press through the microphone stream, the
recognizers, cleanup and the paste into a text field. The fakes are only at
the edge of the process; tests/fake_platform.py describes them.

The state is a fresh install: the shipped templates copied the way setup.sh
copies them, default preferences, and the default Mac model set (Parakeet and
Whisper Tiny, no large-v3-turbo, no cleanup model).
"""

import json
import sys
import tempfile
import time
import traceback
import unittest
from pathlib import Path

import fake_platform
from fake_platform import Key, TextField


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "benchmarks" / "default_path_sentences.json"
MAC = None


def setUpModule():
    global MAC
    if sys.platform != "darwin":
        raise unittest.SkipTest(
            "the harness boots the macOS runtime path of dictate.py")
    MAC = fake_platform.boot_fresh_install()


def tearDownModule():
    if MAC is not None:
        MAC.close()


def booted() -> fake_platform.FakeMac:
    if MAC.boot_error is not None:
        raise AssertionError(
            "main() did not reach the event loop:\n" + "".join(
                traceback.format_exception(MAC.boot_error)))
    if not MAC.wait_until(MAC.warmed_up, 10.0):
        raise AssertionError("warm-up did not finish\n" + MAC.log_tail())
    return MAC


def transcript_records() -> list[dict]:
    path = MAC.dictate.TRANSCRIPTS_FILE
    if not path.exists():
        return []
    return [json.loads(line) for line in path.read_text().splitlines()
            if line.strip()]


def load_corpus() -> list[dict]:
    return json.loads(CORPUS.read_text(encoding="utf-8"))["cases"]


class ImportTests(unittest.TestCase):
    def test_the_checkout_module_runs_on_the_fake_platform(self):
        dictate = MAC.dictate
        if dictate is None:
            booted()
        source = (ROOT / "dictate.py").resolve()
        self.assertEqual(Path(dictate.__file__).resolve(), source)
        self.assertEqual(
            Path(dictate.finish_and_process.__code__.co_filename).resolve(),
            source)
        self.assertIs(dictate.sd, sys.modules["sounddevice"])
        self.assertIsInstance(sys.modules["sounddevice"],
                              fake_platform.FakeModule)

    def test_private_state_is_the_shipped_templates_in_a_fresh_install(self):
        dictate = MAC.dictate
        if dictate is None:
            booted()
        state_files = {name: value for name, value in vars(dictate).items()
                       if name.endswith("_FILE") and isinstance(value, Path)}
        self.assertIn("PREFERENCES_FILE", state_files)
        for name, path in state_files.items():
            with self.subTest(name=name):
                self.assertEqual(path.parent, MAC.state)
        templates = json.loads(
            (ROOT / "snippets.template.json").read_text(encoding="utf-8"))
        self.assertEqual(dictate._load_snippet_map(), templates)
        self.assertEqual(dictate.PREFERENCES["hotkey"], "alt_r")
        for _template, name in fake_platform.FRESH_INSTALL_TEMPLATES:
            with self.subTest(file=name):
                mode = (MAC.state / name).stat().st_mode & 0o777
                self.assertEqual(mode, 0o600)


class BootTests(unittest.TestCase):
    """main() wires the app together; these run it to the event loop."""

    def test_main_reaches_the_event_loop(self):
        mac = booted()
        loops = [call for call in mac.desktop.main_thread_calls
                 if call[0] == "runEventLoop"]
        self.assertEqual(len(loops), 1, mac.log_tail())

    def test_the_window_is_built_with_every_action_main_wires(self):
        mac = booted()
        gui_module = sys.modules["whisper_face_gui"]
        gui = mac.dictate.STATUS["bar"].gui
        self.assertIsInstance(gui, gui_module.WhisperFaceGUI)
        actions = gui.view_model.actions
        self.assertIsInstance(actions, gui_module.GUIActions)
        for name in gui_module.GUIActions.__dataclass_fields__:
            with self.subTest(action=name):
                self.assertTrue(callable(getattr(actions, name)))

    def test_the_window_switch_for_actually_reaches_the_runtime(self):
        # The window's default action answers False and stores nothing, so a
        # switch main() forgot to wire looks fine and snaps back to Off.
        mac = booted()
        dictate = mac.dictate
        actions = dictate.STATUS["bar"].gui.view_model.actions
        spoken = "Let's meet Tuesday, actually Wednesday."

        def typed(sentence):
            return mac.dictate_sentence(sentence, field=TextField()).text

        self.assertIs(dictate.PREFERENCES["self_correction"], False)
        self.assertEqual(typed(spoken), spoken)
        try:
            self.assertIs(actions.set_self_correction(True), True)
            self.assertIs(dictate.PREFERENCES["self_correction"], True)
            self.assertIs(actions.settings_snapshot()["self_correction"], True)
            self.assertEqual(typed(spoken), "Let's meet Wednesday.")
        finally:
            dictate.set_self_correction(False)
        self.assertIs(actions.settings_snapshot()["self_correction"], False)
        self.assertEqual(typed(spoken), spoken)

    def test_the_capture_key_is_heard_by_a_running_listener(self):
        mac = booted()
        listeners = [listener for listener in mac.desktop.listeners
                     if listener.running]
        self.assertEqual(len(listeners), 1)
        self.assertTrue(callable(listeners[0].on_press))
        self.assertTrue(callable(listeners[0].on_release))

    def test_the_microphone_opens_mono_at_the_capture_rate(self):
        mac = booted()
        self.assertGreaterEqual(len(mac.desktop.streams), 2)
        for stream in mac.desktop.streams:
            with self.subTest(stream=id(stream)):
                self.assertEqual(stream.options["samplerate"], 16_000)
                self.assertEqual(stream.options["channels"], 1)
                self.assertEqual(stream.options["dtype"], "float32")
                self.assertTrue(callable(stream.callback))

    def test_the_desktop_endpoint_listens_on_loopback_only(self):
        mac = booted()
        self.assertEqual(
            [server.server_address for server in mac.desktop.servers],
            [("127.0.0.1", 8787)])

    def test_a_fresh_install_speaks_to_no_network_and_no_unlisted_process(self):
        mac = booted()
        self.assertEqual(mac.dictate.PIPELINE_STATE["cleanup_status"],
                         "Unavailable")
        self.assertEqual(mac.desktop.network, [])
        helper = [argv for argv in mac.desktop.helper_spawns]
        self.assertEqual(len(helper), 1)
        self.assertEqual(helper[0][1:], ["--server"])
        # Every other process the runtime asks for, by program. A new entry
        # here is a new thing the app runs on the user's Mac.
        allowed = {"afplay", "/bin/launchctl", "/bin/ps"}
        self.assertLessEqual(
            {argv[0] for argv in mac.desktop.spawns}, allowed)


    def test_before_permissions_are_granted_nothing_listens_or_records(self):
        # The first launch of a fresh install has neither Input Monitoring
        # nor Accessibility. That is a second boot, so it runs in a child.
        booted()
        child = fake_platform.REAL_POPEN(
            [sys.executable, "-c", PERMISSIONS_MISSING, str(ROOT / "tests")],
            stdout=fake_platform.subprocess.PIPE,
            stderr=fake_platform.subprocess.PIPE, text=True)
        stdout, stderr = child.communicate(timeout=60)
        self.assertEqual(child.returncode, 0, stderr)
        result = json.loads(stdout.splitlines()[-1])
        self.assertIsNone(result["boot_error"], result["log"])
        self.assertTrue(result["event_loop"])
        self.assertTrue(result["window"])
        self.assertTrue(result["rechecking"])
        self.assertEqual(result["listeners"], 0)
        self.assertEqual(result["streams"], 0)
        self.assertEqual(result["network"], [])


PERMISSIONS_MISSING = """
import json, sys, threading
sys.path.insert(0, sys.argv[1])
import fake_platform
mac = fake_platform.FakeMac()
mac.desktop.trusted = False
mac.boot()
desktop = mac.desktop
result = {
    "boot_error": repr(mac.boot_error) if mac.boot_error else None,
    "event_loop": any(call[0] == "runEventLoop"
                      for call in desktop.main_thread_calls),
    "window": mac.dictate is not None
              and mac.dictate.STATUS["bar"].gui is not None,
    "rechecking": any(thread.name == "whisper-face-permission-recheck"
                      for thread in threading.enumerate()),
    "listeners": len(desktop.listeners),
    "streams": len(desktop.streams),
    "network": desktop.network,
    "log": mac.log_tail(),
}
mac.close()
print(json.dumps(result))
"""


class FreshInstallDictationTests(unittest.TestCase):
    """End to end: a key press to the text in the field."""

    def test_a_dictation_is_pasted_and_proved_by_reading_the_field(self):
        mac = booted()
        heard = len(mac.desktop.sounds())
        field = mac.dictate_sentence(
            "The slides look great.", field=TextField())
        self.assertEqual(field.text, "The slides look great.")
        self.assertEqual(
            mac.dictate.PIPELINE_STATE["last_asr_engine"], "parakeet-unified")
        self.assertEqual(
            mac.dictate.PIPELINE_STATE["last_insertion_state"], "verified")
        self.assertEqual(mac.desktop.sounds()[heard:], ["Tink", "Pop"])
        self.assertEqual(transcript_records()[-1]["clean"],
                         "The slides look great.")

    def test_a_second_dictation_joins_the_first_with_one_space(self):
        mac = booted()
        field = TextField()
        mac.dictate_sentence("The build is green.", field=field)
        mac.dictate_sentence("Ship it.", field=field)
        self.assertEqual(field.text, "The build is green. Ship it.")

    def test_dictated_text_is_private_on_the_clipboard_and_then_returned(self):
        mac = booted()
        mac.desktop.pasteboard.copy_text("my own clipboard")
        mac.dictate_sentence("Lunch is at one.", field=TextField())
        paste = mac.desktop.pastes[-1]
        self.assertEqual(paste["text"], "Lunch is at one.")
        self.assertIn("org.nspasteboard.ConcealedType", paste["types"])
        self.assertIn("org.nspasteboard.TransientType", paste["types"])
        self.assertIn(mac.dictate.PASTEBOARD_CURRENT_HOST_ONLY,
                      paste["options"])
        self.assertTrue(mac.wait_until(
            lambda: mac.desktop.pasteboard.stringForType_(
                fake_platform.Pasteboard.STRING) == "my own clipboard",
            3.0), "the user's clipboard was not given back")

    def test_secrets_on_screen_never_reach_the_recognizer(self):
        mac = booted()
        desktop = mac.desktop

        def prompts_while_dictating(field):
            before = len(desktop.whisper_requests)
            mac.dictate_sentence("Done.", field=field)
            return " ".join(request.get("initial_prompt") or ""
                            for request in desktop.whisper_requests[before:])

        # What the user can see biases the recognizer...
        desktop.pasteboard.copy_text("Ask Okonkwo about it.")
        prompt = prompts_while_dictating(TextField("Meet Quellmeyer at noon"))
        self.assertIn("Okonkwo", prompt)
        self.assertIn("Quellmeyer", prompt)
        # ...except what a password field holds...
        password = TextField("Zxqvlarbinth")
        password.secure = True
        self.assertNotIn("Zxqvlarbinth", prompts_while_dictating(password))
        # ...and what a password manager copied.
        desktop.pasteboard.copy_text("Ask Vorbeckhauser about it.",
                                     concealed=True)
        self.assertNotIn("Vorbeckhauser",
                         prompts_while_dictating(TextField("Notes")))

    def test_text_after_an_emoji_is_pasted_and_verified(self):
        # Accessibility counts the caret in UTF-16 units, and the emoji
        # before it is four of them but two characters.
        mac = booted()
        field = TextField("Thanks 👍🏽")
        mac.dictate_sentence("See you soon.", field=field)
        self.assertEqual(field.text, "Thanks 👍🏽 See you soon.")
        self.assertEqual(
            mac.dictate.PIPELINE_STATE["last_insertion_state"], "verified")

    def test_a_dictation_into_the_middle_of_text_follows_the_caret(self):
        mac = booted()
        field = TextField("Thanks 👍🏽 and bye", caret=len("Thanks 👍🏽 "))
        mac.dictate_sentence("See you soon.", field=field)
        self.assertTrue(field.text.startswith("Thanks 👍🏽 See you soon."),
                        field.text)
        self.assertTrue(field.text.endswith("and bye"), field.text)
        self.assertEqual(
            mac.dictate.PIPELINE_STATE["last_insertion_state"], "verified")

    @unittest.expectedFailure
    def test_a_dictation_into_the_middle_of_text_is_spaced_after(self):
        # Insertion review (non-blocking point 7): only the separator before
        # the insertion point is decided, so a sentence dictated in front of
        # existing words runs into them ("soon.and bye").
        mac = booted()
        field = TextField("Thanks 👍🏽 and bye", caret=len("Thanks 👍🏽 "))
        mac.dictate_sentence("See you soon.", field=field)
        self.assertEqual(field.text, "Thanks 👍🏽 See you soon. and bye")

    def test_actually_corrects_only_after_the_user_turns_it_on(self):
        mac = booted()
        dictate = mac.dictate
        spoken = "Let's meet Tuesday, actually Wednesday."

        def typed(sentence):
            return mac.dictate_sentence(sentence, field=TextField()).text

        # A fresh install starts from the shipped template, which says off.
        self.assertIs(json.loads(
            (ROOT / "preferences.template.json").read_text())[
                "self_correction"], False)
        self.assertIs(dictate.gui_settings_snapshot()["self_correction"],
                      False)
        self.assertEqual(typed(spoken), spoken)
        self.assertIs(dictate.set_self_correction(True), True)
        try:
            self.assertIs(
                dictate.gui_settings_snapshot()["self_correction"], True)
            self.assertIs(json.loads(
                dictate.PREFERENCES_FILE.read_text())["self_correction"],
                True)
            self.assertEqual(typed(spoken), "Let's meet Wednesday.")
            self.assertEqual(dictate.PIPELINE_STATE["last_cleanup_edits"],
                             ["self_correction"])
            # Turned on, the rule is the one wave 1 shipped: it corrects
            # between two like items and nowhere else.
            for sentence, expected in (
                ("The total is 45 dollars, actually 54.",
                 "The total is 54 dollars."),
                ("Call me at 3 pm, actually 4 pm.", "Call me at 4 pm."),
                ("I didn't actually finish the report.",
                 "I didn't actually finish the report."),
                ("It's not 5, actually 6.", "It's not 5, actually 6."),
                ("The shop is open from 9 to 5, actually 9 to 6.",
                 "The shop is open from 9 to 5, actually 9 to 6."),
            ):
                with self.subTest(spoken=sentence):
                    self.assertEqual(typed(sentence), expected)
        finally:
            self.assertIs(dictate.set_self_correction(False), False)
        self.assertIs(json.loads(
            dictate.PREFERENCES_FILE.read_text())["self_correction"], False)
        self.assertEqual(typed(spoken), spoken)

    def test_a_recognizer_stuck_on_one_word_is_cut_short(self):
        mac = booted()
        field = mac.dictate_sentence(
            "Restart " + "Unraid " * 40 + "tonight.", field=TextField())
        self.assertEqual(field.text, "Restart Unraid Unraid tonight.")

    def test_a_short_echo_of_the_glossary_prompt_is_not_typed(self):
        # A recognizer primed with the user's terms reads them back when
        # it hears no speech, often as one term a few times over. That is
        # too short to cut as a loop and is still not dictation.
        mac = booted()
        dictate = mac.dictate
        with dictate.GLOSS["lock"]:
            terms = dictate.GLOSS["terms"]
            dictate.GLOSS["terms"] = ["Unraid", "Proxmox"]
        try:
            for echoed in (
                "Unraid, Unraid, Unraid, Unraid.",
                "Unraid Unraid Unraid Unraid Unraid Unraid Unraid",
            ):
                with self.subTest(echoed=echoed):
                    field = TextField("Notes: ")
                    pastes = len(mac.desktop.pastes)
                    mac.dictate_sentence(echoed, field=field)
                    self.assertEqual(field.text, "Notes: ")
                    self.assertEqual(len(mac.desktop.pastes), pastes)
                    self.assertEqual(
                        dictate.CAPTION["text"],
                        "That didn't sound like dictation — try again")
            # The same terms in a sentence, and a word said three times
            # for emphasis, are dictation.
            for spoken in (
                "Move Unraid to Proxmox tonight.",
                "That was very, very, very good.",
            ):
                with self.subTest(spoken=spoken):
                    field = mac.dictate_sentence(spoken, field=TextField())
                    self.assertEqual(field.text, spoken)
        finally:
            with dictate.GLOSS["lock"]:
                dictate.GLOSS["terms"] = terms

    def test_edit_without_the_cleanup_model_leaves_the_selection_alone(self):
        # A default install has no rewrite model. What was dictated is an
        # instruction about the selection, never text to put in its place.
        mac = booted()
        text = "Please send the report by Friday."
        field = TextField(text)
        field.selection = (len("Please send "), len("the report"))
        pastes = len(mac.desktop.pastes)
        heard = len(mac.desktop.sounds())
        records = len(transcript_records())
        mac.dictate_sentence(
            "Make this more formal.", field=field, modifiers=(Key.cmd,))
        self.assertEqual(field.text, text)
        self.assertEqual(field.selection,
                         (len("Please send "), len("the report")))
        self.assertEqual(len(mac.desktop.pastes), pastes)
        self.assertEqual(
            mac.dictate.CAPTION["text"],
            "Edit mode needs the cleanup model — nothing changed")
        self.assertEqual(mac.desktop.sounds()[heard:], ["Tink", "Funk"])
        self.assertEqual(len(transcript_records()), records)
        self.assertEqual(mac.desktop.network, [])

    def test_compose_and_reply_without_the_cleanup_model_type_nothing(self):
        mac = booted()
        for mode, key, spoken in (
            ("Compose", Key.shift,
             "Write a short note declining the meeting."),
            ("Reply", Key.ctrl, "Tell her I agree with the plan."),
        ):
            with self.subTest(mode=mode):
                field = TextField("Can we meet on Thursday? ")
                pastes = len(mac.desktop.pastes)
                mac.dictate_sentence(spoken, field=field, modifiers=(key,))
                self.assertEqual(field.text, "Can we meet on Thursday? ")
                self.assertEqual(len(mac.desktop.pastes), pastes)
                self.assertEqual(
                    mac.dictate.CAPTION["text"],
                    f"{mode} mode needs the cleanup model — nothing changed")
        # Plain dictation is unaffected, straight afterwards.
        field = mac.dictate_sentence(
            "Thursday works for me.", field=TextField())
        self.assertEqual(field.text, "Thursday works for me.")

    def test_a_filled_snippet_expands_only_when_asked_for(self):
        mac = booted()
        template = json.loads(
            (ROOT / "snippets.template.json").read_text())["email"]
        mac.dictate.save_gui_snippet("email", template, "sam@example.com")
        try:
            cases = (
                ("What is your email address?",
                 "What is your email address?"),
                ("Please insert my email here.",
                 "Please sam@example.com here."),
                ("Insert my email.", "sam@example.com"),
            )
            for spoken, expected in cases:
                with self.subTest(spoken=spoken):
                    field = mac.dictate_sentence(spoken, field=TextField())
                    self.assertEqual(field.text, expected)
        finally:
            mac.dictate.save_gui_snippet("email", "sam@example.com", template)

    def test_whisper_tiny_never_overrules_parakeet(self):
        # A default install has no large-v3-turbo, so nothing can break a
        # tie between Parakeet and the Tiny cross-check: Parakeet stands.
        mac = booted()
        field = mac.dictate_sentence(
            "Ask Gwen to review the Qwen model.",
            whisper="Has went through the queue mode.", field=TextField())
        self.assertEqual(field.text, "Ask Gwen to review the Qwen model.")

    def test_on_screen_context_never_replaces_an_ordinary_word(self):
        # Tiny disagreeing is what lowers Parakeet's confidence on a default
        # install and opens the context gate. Each sentence is dictated with
        # its screen text in every place the runtime reads context from.
        mac = booted()
        desktop = mac.desktop
        rows = (
            ("Looks good.", "Open Code now"),
            ("Not yet.", "Nate"),
            ("Who is on call today?", "AI"),
            ("Keep an eye on it.", "We now route triage through AI."),
            ("Looks fine.", "Finn"),
            ("Code review is at noon.", "Git"),
        )
        tinies = {
            "Looks good.": ("Look good.", "Has went through the queue."),
            "Not yet.": ("Nah yet.", "Has went through the queue."),
            "Who is on call today?": ("Hu is on call to day?",
                                      "Has went through the queue."),
            "Keep an eye on it.": ("Has went through the queue.",),
            "Looks fine.": ("Look fine.",),
            "Code review is at noon.": ("Has went through the queue.",),
        }

        def selection(screen, _directory):
            field = TextField(screen)
            field.selection = (0, len(screen))
            return field, ""

        def focused_field(screen, _directory):
            return TextField(screen), screen + " "

        def clipboard(screen, _directory):
            desktop.pasteboard.copy_text(screen)
            return TextField(), ""

        def document(screen, directory):
            path = directory / "notes.md"
            path.write_text(screen + "\n", encoding="utf-8")
            field = TextField(bundle="com.microsoft.VSCode")
            field.document = path.as_uri()
            return field, ""

        def repository(screen, directory):
            path = directory / "notes.md"
            path.write_text("", encoding="utf-8")
            for name in screen.split():
                (directory / name).mkdir()
            field = TextField(bundle="com.microsoft.VSCode")
            field.document = path.as_uri()
            return field, ""

        sources = {"selection": selection, "field": focused_field,
                   "clipboard": clipboard, "document": document,
                   "repository": repository}
        try:
            for spoken, screen in rows:
                for tiny in tinies[spoken]:
                    for source, place in sources.items():
                        with tempfile.TemporaryDirectory() as directory, \
                                self.subTest(spoken=spoken, tiny=tiny,
                                             source=source):
                            desktop.pasteboard.copy_text("")
                            field, before = place(screen, Path(directory))
                            mac.dictate_sentence(
                                spoken, whisper=tiny, field=field)
                            self.assertEqual(field.text, before + spoken)
        finally:
            desktop.pasteboard.copy_text("")

    def test_on_screen_context_repairs_a_name_only_when_unsure(self):
        mac = booted()
        for tiny, expected in (
                ("As when two view it.", "Ask Qwen to review it."),
                ("Ask Gwen to review it.", "Ask Gwen to review it.")):
            with self.subTest(tiny=tiny):
                field = TextField("Qwen")
                field.selection = (0, len("Qwen"))
                mac.dictate_sentence(
                    "Ask Gwen to review it.", whisper=tiny, field=field)
                self.assertEqual(field.text, expected)

    def test_a_transcript_tiny_contradicted_is_not_recorded_as_verified(self):
        # No large-v3-turbo on a default install, so nothing settles the
        # disagreement: Parakeet's words are typed, unverified.
        mac = booted()
        field = mac.dictate_sentence(
            "Ask Gwen to review it.",
            whisper="Has went through the queue.", field=TextField())
        self.assertEqual(field.text, "Ask Gwen to review it.")
        self.assertIs(
            transcript_records()[-1]["metrics"]["asr_verified"], False)

    def test_the_transcript_records_the_readback_of_its_own_dictation(self):
        mac = booted()
        mac.dictate_sentence(
            "Notes for Friday.",
            field=TextField(rewrite=lambda pasted: pasted + "\n"))
        mac.dictate_sentence("All clear.", field=TextField())
        records = transcript_records()
        self.assertEqual(
            [record["metrics"]["readback_shape"] for record in records[-2:]],
            ["trailing-whitespace", ""])

    def test_the_undo_key_removes_exactly_the_last_dictation(self):
        mac = booted()
        decision = mac.dictate.set_undo_hotkey("f5")
        self.assertTrue(decision["accepted"], decision)
        try:
            field = TextField("Draft 🙂 ")
            mac.dictate_sentence("This line goes away.", field=field)
            self.assertEqual(field.text, "Draft 🙂 This line goes away.")
            mac.desktop.press(Key.f5)
            mac.desktop.release(Key.f5)
            self.assertTrue(
                mac.wait_until(lambda: field.text == "Draft 🙂 ", 3.0),
                field.text)
        finally:
            mac.dictate.set_undo_hotkey("")

    def test_a_hold_whose_key_up_is_lost_still_finishes(self):
        # A disabled event tap can swallow the key-up. The hold supervisor
        # main() starts reads the key's hardware state instead.
        mac = booted()
        desktop = mac.desktop
        field = TextField()
        desktop.focus(field)
        desktop.heard = {"parakeet": "Lost key still types.",
                         "whisper": "Lost key still types."}
        finished = mac._finished_count
        logged = len(mac.log.getvalue())
        desktop.press(mac.dictate.HOTKEY)
        self.assertTrue(mac.wait_until(desktop.live_streams, 5.0))
        desktop.speak(fake_platform.speech_audio())
        time.sleep(3 * mac.dictate.HOLD_POLL_SECONDS)
        desktop.keys_down.discard(mac.dictate.HOTKEY.value.vk)
        self.assertTrue(
            mac.wait_until(lambda: mac._finished_count > finished, 5.0),
            mac.log_tail())
        self.assertEqual(field.text, "Lost key still types.")
        # Ended by the key's state, not by the stalled-stream check.
        self.assertIn("key release was not seen",
                      mac.log.getvalue()[logged:])
        desktop.release(mac.dictate.HOTKEY)

    def test_a_microphone_that_stops_mid_take_keeps_what_was_heard(self):
        mac = booted()
        desktop = mac.desktop
        field = TextField()
        desktop.focus(field)
        desktop.heard = {"parakeet": "Keep what I said.",
                         "whisper": "Keep what I said."}
        finished = mac._finished_count
        logged = len(mac.log.getvalue())
        desktop.press(mac.dictate.HOTKEY)
        self.assertTrue(mac.wait_until(desktop.live_streams, 5.0))
        desktop.speak(fake_platform.speech_audio())
        # The key stays down and the stream delivers nothing more.
        self.assertTrue(
            mac.wait_until(lambda: mac._finished_count > finished,
                           mac.dictate.NO_AUDIO_SECONDS + 4.0),
            mac.log_tail())
        desktop.release(mac.dictate.HOTKEY)
        self.assertEqual(field.text, "Keep what I said.")
        self.assertIn("microphone stopped delivering audio",
                      mac.log.getvalue()[logged:])

    def test_a_new_default_microphone_is_used_after_a_device_change(self):
        mac = booted()
        desktop = mac.desktop
        desktop.change_default_input("USB Headset")
        self.assertTrue(
            mac.wait_until(lambda: any(
                stream.device == "USB Headset" and not stream.closed
                for stream in desktop.streams), 5.0),
            "a device change reopened nothing on the new default input")
        field = mac.dictate_sentence("New microphone works.",
                                     field=TextField())
        self.assertEqual(field.text, "New microphone works.")
        self.assertEqual(desktop.spoken_into, ["USB Headset"])

    def test_a_listener_that_died_is_replaced_and_the_key_works_again(self):
        mac = booted()
        desktop = mac.desktop
        dead = [listener for listener in desktop.listeners
                if listener.running][0]
        # The listener thread exited on its own; pynput still reads running.
        dead.alive = False
        self.assertTrue(
            mac.wait_until(lambda: len(desktop.listeners) > 1,
                           3 * mac.dictate.HOTKEY_WATCHDOG_INTERVAL),
            mac.log_tail())
        dead.running = False
        field = mac.dictate_sentence("Back in business.", field=TextField())
        self.assertEqual(field.text, "Back in business.")

    def test_replacing_a_listener_while_idle_leaves_the_status_alone(self):
        # Recovery used to abandon a take even when none was active, which
        # reset the menu-bar state and dismissed the HUD: a permission
        # warning or a message the user was reading disappeared each time.
        mac = booted()
        desktop = mac.desktop
        before = len(desktop.listeners)
        marker = len(desktop.main_thread_calls)
        dead = [listener for listener in desktop.listeners
                if listener.running][-1]
        dead.alive = False
        self.assertTrue(
            mac.wait_until(lambda: len(desktop.listeners) > before,
                           3 * mac.dictate.HOTKEY_WATCHDOG_INTERVAL),
            mac.log_tail())
        dead.running = False
        # The worker handles events in order, so the recovery event is done
        # by the time this dictation's press sets the "rec" state.
        field = mac.dictate_sentence("Still here.", field=TextField())
        self.assertEqual(field.text, "Still here.")

        calls = desktop.main_thread_calls[marker:]
        recording = next(
            index for index, (function, args) in enumerate(calls)
            if getattr(function, "__name__", "") == "setState_"
            and args == ("rec",))
        touched = [
            (function.__name__, args)
            for function, args in calls[:recording]
            if getattr(function, "__name__", "") in ("setState_", "dismiss")]
        self.assertEqual(touched, [], mac.log_tail())


class DefaultPathCorpusTests(unittest.TestCase):
    """benchmarks/default_path_sentences.json, typed as written."""

    def test_the_corpus_covers_what_a_fresh_install_gets_wrong_easily(self):
        cases = load_corpus()
        self.assertGreaterEqual(len(cases), 60)
        self.assertEqual(len({case["id"] for case in cases}), len(cases))
        covers = {case["covers"] for case in cases}
        for kind in ("actually-adverb", "actually-contrast", "snippet-name",
                     "sign-off", "numbers", "scratch-that", "new-line",
                     "tone-word-opening", "repeats", "emoji"):
            with self.subTest(covers=kind):
                self.assertIn(kind, covers)
        adverbs = [case for case in cases if case["covers"] == "actually-adverb"]
        corrections = [case for case in cases
                       if case["covers"] == "actually-correction"]
        # A rule that deletes words needs more must-not-fire cases than
        # must-fire ones.
        self.assertGreater(len(adverbs), len(corrections))
        # The correction is opt-in, so on a fresh install every sentence
        # with "actually" in it is typed as spoken.
        for case in cases:
            if "actually" in case["spoken"].casefold():
                with self.subTest(case=case["id"]):
                    self.assertIn(case["spoken"].rstrip("."),
                                  case["expected"])
        snippets = json.loads(
            (ROOT / "snippets.template.json").read_text(encoding="utf-8"))
        spoken = " ".join(case["spoken"].casefold() for case in cases)
        for name in snippets:
            with self.subTest(snippet=name):
                self.assertIn(name.casefold(), spoken)

    def test_every_sentence_arrives_as_written(self):
        mac = booted()
        for case in load_corpus():
            if case.get("known_failure"):
                continue
            field = TextField(case.get("field", ""),
                              bundle=case.get("app", "com.apple.TextEdit"))
            # A dictation that cannot start or finish fails the whole test
            # at once rather than once per sentence.
            mac.dictate_sentence(case["spoken"], field=field)
            with self.subTest(case=case["id"]):
                self.assertEqual(field.text, case["expected"])


def _known_failure_test(case):
    def test(self):
        mac = booted()
        field = TextField(case.get("field", ""),
                          bundle=case.get("app", "com.apple.TextEdit"))
        mac.dictate_sentence(case["spoken"], field=field)
        self.assertEqual(field.text, case["expected"])

    test.__doc__ = case["known_failure"]
    return unittest.expectedFailure(test)


# Cases the default path still gets wrong. Each names its defect in the
# corpus; the test keeps failing until the defect is fixed, and then fails as
# an unexpected success until the case loses its "known_failure".
for _case in load_corpus():
    if _case.get("known_failure"):
        setattr(DefaultPathCorpusTests,
                "test_known_failure_" + _case["id"].replace("-", "_"),
                _known_failure_test(_case))


if __name__ == "__main__":
    unittest.main()
