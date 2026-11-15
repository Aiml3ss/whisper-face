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
import threading
import time
import traceback
import unittest
from pathlib import Path

import numpy as np

import fake_platform
from fake_platform import Key, TextField


ROOT = Path(__file__).resolve().parents[1]
CORPUS = ROOT / "benchmarks" / "default_path_sentences.json"
MAC = None
SHIPPED_SWITCHES: dict = {}
SHIPPED_RUNTIME: dict = {}

sys.path.append(str(ROOT / "benchmarks"))
import recognizer_replay  # noqa: E402


def setUpModule():
    global MAC
    if sys.platform != "darwin":
        raise unittest.SkipTest(
            "the harness boots the macOS runtime path of dictate.py")
    MAC = fake_platform.boot_fresh_install()
    # What the module ships with, read before any test changes it.
    SHIPPED_SWITCHES.update(
        prosody=MAC.dictate.PROSODY_FORMATTING,
        routing=MAC.dictate.PARAKEET_WORD_CONFIDENCE_ROUTING,
        protocol=MAC.dictate.PARAKEET_PROTOCOL)
    SHIPPED_RUNTIME.update(
        runtime=MAC.dictate.PARAKEET_RUNTIME,
        asked_first=MAC.dictate.PARAKEET.preferred)


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
        # The FluidAudio helper: Whisper Face's own runtime is installed
        # beside it but, with its switch off as shipped, never started.
        self.assertEqual(Path(helper[0][0]).name, fake_platform.HELPER_NAME)
        self.assertTrue(mac.dictate.PARAKEET_RUNTIME_HELPER.is_file())
        # Protocol 2 is asked for, and the models folder Python verified is
        # named; a protocol 1 helper reads only "--server".
        self.assertEqual(helper[0][1:], [
            "--server", "--protocol", "2",
            "--models", str(mac.dictate.PARAKEET_MODEL_DIR)])
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

    def test_a_phrase_said_twice_by_accident_is_typed_once(self):
        # The owner's text to their wife, exactly as it came out, through
        # the whole program into Messages on a fresh install.
        mac = booted()
        dictate = mac.dictate
        spoken = "Of course I can. I have nothing I have nothing going on"

        def typed(sentence, bundle="com.apple.MobileSMS", modifiers=()):
            field = TextField(bundle=bundle)
            return mac.dictate_sentence(
                sentence, field=field, modifiers=modifiers).text

        # A fresh install starts from the shipped template, which says on.
        self.assertIs(json.loads(
            (ROOT / "preferences.template.json").read_text())[
                "remove_repeats"], True)
        self.assertIs(dictate.PREFERENCES["remove_repeats"], True)
        self.assertIs(dictate.gui_settings_snapshot()["remove_repeats"],
                      True)
        self.assertEqual(typed(spoken),
                         "Of course I can. I have nothing going on")
        # Counted like every other cleanup edit, for the evidence inspector.
        self.assertEqual(dictate.PIPELINE_STATE["last_cleanup_edits"],
                         ["remove_repeat"])
        self.assertEqual(
            dictate.runtime_status_snapshot()["last_cleanup_edits"],
            ["remove_repeat"])
        # Three times is emphasis and arrives whole (a chat app drops only
        # the closing full stop).
        self.assertEqual(typed("No way, no way, no way."),
                         "No way, no way, no way")
        # A verbatim app and code mode get the words as they were said.
        self.assertEqual(
            typed("I I think so", bundle="com.apple.Terminal"),
            "I I think so")
        self.assertEqual(
            typed("the the cat", bundle="com.apple.TextEdit",
                  modifiers=(Key.shift, Key.ctrl)),
            "the the cat")

        actions = dictate.STATUS["bar"].gui.view_model.actions
        try:
            # The window's switch reaches the runtime and the file.
            self.assertIs(actions.set_remove_repeats(False), False)
            self.assertIs(dictate.PREFERENCES["remove_repeats"], False)
            self.assertIs(json.loads(
                dictate.PREFERENCES_FILE.read_text())["remove_repeats"],
                False)
            self.assertIs(actions.settings_snapshot()["remove_repeats"],
                          False)
            self.assertEqual(typed(spoken), spoken)
            self.assertEqual(dictate.PIPELINE_STATE["last_cleanup_edits"],
                             [])
        finally:
            self.assertIs(actions.set_remove_repeats(True), True)
        self.assertIs(json.loads(
            dictate.PREFERENCES_FILE.read_text())["remove_repeats"], True)
        self.assertEqual(typed(spoken),
                         "Of course I can. I have nothing going on")

    def test_the_repeat_repair_does_not_run_again_on_the_model_text(self):
        # The optional cleanup model may change punctuation alone, and the
        # voice compiler accepts that because the words stay the same. A
        # full stop the speaker said between two copies, turned into a
        # comma, must not let a copy be deleted after the model, where no
        # record would show it.
        mac = booted()
        dictate = mac.dictate
        spoken = "I want to. I want to ask you something, you know."
        asked = []

        def model(text, tone, mode="capture", context=None):
            asked.append(text)
            out = text.replace("to. I want", "to, I want")
            edits = [dictate.CleanupEdit(
                "punctuation", "to. I want", "to, I want")] \
                if out != text else []
            return out, edits

        saved = (dictate.llm_clean_with_edits,
                 dictate.delayed_cleanup_scheduling_enabled)
        dictate.llm_clean_with_edits = model
        dictate.delayed_cleanup_scheduling_enabled = lambda: False
        try:
            field = mac.dictate_sentence(
                spoken, field=TextField(bundle="com.apple.TextEdit"))
        finally:
            (dictate.llm_clean_with_edits,
             dictate.delayed_cleanup_scheduling_enabled) = saved
        self.assertEqual(asked, [spoken])
        self.assertEqual(
            field.text, "I want to, I want to ask you something, you know.")
        self.assertEqual(dictate.PIPELINE_STATE["last_cleanup_edits"],
                         ["proof:punctuation"])

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


class RecognizerReplayTests(unittest.TestCase):
    """benchmarks/recognizer_transcripts.json, replayed through the program.

    Each transcript is what the real Parakeet helper wrote for a sentence
    the macOS synthesizer spoke. That shows how the recognizer punctuates,
    capitalises and formats numbers; it says nothing about how accurately it
    hears a person. benchmarks/recognizer_replay.py records the file and
    runs the same replay as its check command.
    """

    def test_every_case_is_recorded_and_has_texts_to_accept(self):
        data = recognizer_replay.load()
        self.assertEqual(recognizer_replay.problems(data), [])
        cases = data["cases"]
        self.assertGreaterEqual(len(cases), 76)
        for kind in ("actually-adverb", "actually-correction",
                     "actually-contrast", "new-line", "scratch-that",
                     "sign-off", "numbers", "repeats", "fillers"):
            with self.subTest(covers=kind):
                self.assertIn(kind, {case["covers"] for case in cases})
        # The correction is opt-in, so on a fresh install every sentence the
        # recognizer wrote "actually" in keeps it.
        for case in cases:
            if "actually" in case["transcript"].casefold():
                for text in case["accept"]:
                    with self.subTest(case=case["id"], accept=text):
                        self.assertIn("actually", text.casefold())

    def test_every_transcript_arrives_as_accepted(self):
        mac = booted()
        for case in recognizer_replay.load_cases():
            if case.get("known_failure"):
                continue
            # A dictation that cannot start or finish fails the whole test
            # at once rather than once per sentence.
            typed = recognizer_replay.type_transcript(mac, case)
            with self.subTest(case=case["id"], said=case["said"]):
                self.assertTrue(
                    recognizer_replay.arrives_as_accepted(case, typed),
                    f"typed {typed!r} for {case['transcript']!r}; "
                    f"accept {case['accept']!r}")


def _known_replay_failure_test(case):
    def test(self):
        typed = recognizer_replay.type_transcript(booted(), case)
        self.assertIn(typed, case["accept"])

    test.__doc__ = case["known_failure"]
    return unittest.expectedFailure(test)


for _case in recognizer_replay.load_cases():
    if _case.get("known_failure"):
        setattr(RecognizerReplayTests,
                "test_known_failure_" + _case["id"].replace("-", "_"),
                _known_replay_failure_test(_case))


BASELINE = ROOT / "benchmarks" / "replay_text_and_route_baseline.json"


class HelperWordEvidenceTests(unittest.TestCase):
    """The helper's word evidence changes nothing a user sees while it is off.

    The client asks every helper for protocol 2. A protocol 1 helper ignores
    the request; a protocol 2 helper adds each word's time and confidence,
    which the runtime parses, reshapes and records. Until one of the two
    switches is turned on, typed text and the consequence route must be what
    origin/main produced: benchmarks/replay_text_and_route_baseline.json.
    """

    def tearDown(self):
        mac = MAC
        mac.desktop.helper_protocol = 2
        mac.desktop.helper_words_override = None
        mac.dictate.PROSODY_FORMATTING = False
        mac.dictate.PARAKEET_WORD_CONFIDENCE_ROUTING = False
        mac.dictate.PARAKEET._close()

    @staticmethod
    def use_helper(mac, protocol: int) -> None:
        mac.desktop.helper_protocol = protocol
        # The next decode starts a new helper, which speaks this protocol.
        mac.dictate.PARAKEET._close()

    @staticmethod
    def dictate_case(mac, key: str, case: dict) -> dict:
        if key.startswith("default:"):
            field = TextField(case.get("field", ""),
                              bundle=case.get("app", "com.apple.TextEdit"))
            typed = mac.dictate_sentence(case["spoken"], field=field).text
        else:
            typed = recognizer_replay.type_transcript(mac, case)
        metrics = transcript_records()[-1]["metrics"]
        return {
            "typed": typed,
            "route": mac.dictate.PIPELINE_STATE["last_consequence_route"],
            "status": metrics["word_evidence_status"],
            "words": metrics["word_evidence"],
        }

    def test_the_word_evidence_switches_ship_off(self):
        # Read at boot: by now earlier tests' tearDown has reset the module.
        booted()
        self.assertEqual(
            SHIPPED_SWITCHES,
            {"prosody": False, "routing": False, "protocol": 2})

    def test_every_replayed_sentence_types_and_routes_as_origin_main(self):
        mac = booted()
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["cases"]
        cases = [("default:" + case["id"], case) for case in load_corpus()]
        cases += [("replay:" + case["id"], case)
                  for case in recognizer_replay.load_cases()]
        recorded = [key for key, _case in cases if key in baseline]
        # A corpus case without a recorded baseline is still compared
        # between the two helpers below, but cannot show it is unchanged.
        self.assertGreaterEqual(len(recorded), 197)
        results = {}
        for protocol in (1, 2):
            self.use_helper(mac, protocol)
            for key, case in cases:
                results[protocol, key] = self.dictate_case(mac, key, case)
        for key, _case in cases:
            v1, v2 = results[1, key], results[2, key]
            with self.subTest(case=key):
                self.assertEqual((v2["typed"], v2["route"]),
                                 (v1["typed"], v1["route"]))
                if key in baseline:
                    self.assertEqual(
                        (v1["typed"], v1["route"]),
                        (baseline[key]["typed"], baseline[key]["route"]))
                # The words did arrive: an identity that held only because
                # every word was thrown away would prove nothing.
                self.assertEqual((v1["status"], v1["words"]),
                                 ("v1-helper", 0))
                self.assertEqual(v2["status"], "native")
                self.assertGreater(v2["words"], 0)
        spawned = mac.desktop.helper_spawns[-1][1:]
        self.assertEqual(spawned[:3], ["--server", "--protocol", "2"])

    def test_malformed_words_are_dropped_and_the_text_is_kept(self):
        mac = booted()
        spoken = "Send the invoice to Dana by Friday."
        # Whisper mishears, so a helper that failed and fell back to Whisper
        # would change the typed text, not only the recorded status.
        misheard = "Send the invoice to Donna by Friday."
        expected = mac.dictate_sentence(
            spoken, whisper=misheard, field=TextField()).text
        self.assertIn("Dana", expected)
        for name, words in (
                ("not a list", {"word": "Send"}),
                ("time out of order", [
                    {"word": "Send", "start": 0.5, "end": 0.6,
                     "confidence": 0.9},
                    {"word": "the", "start": 0.1, "end": 0.2,
                     "confidence": 0.9}]),
                ("past the end", [{"word": "Send", "start": 0.1,
                                   "end": 900.0, "confidence": 0.9}]),
                ("confidence above one", [{"word": "Send", "start": 0.1,
                                           "end": 0.2, "confidence": 7}]),
                # Crosses the pipe as 401 digits; float() of it raises.
                ("integer too large", [{"word": "Send", "start": 10 ** 400,
                                        "end": 0.2, "confidence": 0.9}]),
                ("negative integer too large", [
                    {"word": "Send", "start": 0.1, "end": 0.2,
                     "confidence": -(10 ** 400)}]),
                ("absurd count", [{"word": "a", "start": 0.0, "end": 0.0,
                                   "confidence": 0.5}] * 5000),
                ("words for other text", [{"word": "Hello", "start": 0.1,
                                           "end": 0.2, "confidence": 0.9}]),
        ):
            with self.subTest(case=name):
                mac.desktop.helper_words_override = words
                typed = mac.dictate_sentence(
                    spoken, whisper=misheard, field=TextField()).text
                metrics = transcript_records()[-1]["metrics"]
                self.assertEqual(typed, expected)
                self.assertEqual(metrics["word_evidence"], 0)
                self.assertEqual(
                    metrics["word_evidence_status"],
                    "misaligned" if name == "words for other text"
                    else "rejected")

    def test_words_of_a_collapsed_decode_loop_are_dropped(self):
        # Collapsing the loop rewrites the text the words were timed for.
        mac = booted()
        field = mac.dictate_sentence(
            "Restart " + "Unraid " * 40 + "tonight.", field=TextField())
        self.assertEqual(field.text, "Restart Unraid Unraid tonight.")
        metrics = transcript_records()[-1]["metrics"]
        self.assertEqual(
            (metrics["word_evidence"], metrics["word_evidence_status"]),
            (0, "misaligned"))

    def test_a_real_pause_reaches_the_text_only_through_its_switch(self):
        mac = booted()
        rate = fake_platform.SAMPLE_RATE
        # Two stretches of speech with 0.7 s of silence between them: long
        # enough for a comma, short of a paragraph.
        audio = np.concatenate([
            fake_platform.speech_audio(1.0, 0.7, seed=3),
            fake_platform.speech_audio(1.2, 0.3, seed=5)])
        spoken = "I called the office and nobody answered."
        timed = [("I", 0.05, 0.25), ("called", 0.3, 0.55), ("the", 0.6, 0.7),
                 ("office", 0.72, 0.95), ("and", 1.75, 1.9),
                 ("nobody", 1.95, 2.3), ("answered.", 2.35, 2.8)]
        self.assertLess(timed[-1][2], len(audio) / rate)
        mac.desktop.helper_words_override = [
            {"word": word, "start": start, "end": end, "confidence": 0.95}
            for word, start, end in timed]
        compiled = []
        compile_voice = mac.dictate.VOICE_COMPILER.compile

        def spy(voice):
            compiled.append(voice)
            return compile_voice(voice)

        mac.dictate.VOICE_COMPILER.compile = spy
        self.addCleanup(setattr, mac.dictate.VOICE_COMPILER, "compile",
                        compile_voice)
        expected = {
            # Off, as shipped: the words are recorded and never compiled.
            (False, False): (spoken, False),
            # Routing alone: the words place spans but add no punctuation.
            (False, True): (spoken, True),
            # Prosody: the pause between "office" and "and" is a comma.
            (True, False): (
                "I called the office, and nobody answered.", True),
        }
        for (prosody, routing), (text, compiled_words) in expected.items():
            with self.subTest(prosody=prosody, routing=routing):
                mac.dictate.PROSODY_FORMATTING = prosody
                mac.dictate.PARAKEET_WORD_CONFIDENCE_ROUTING = routing
                compiled.clear()
                typed = mac.dictate_sentence(
                    spoken, field=TextField(), audio=audio).text
                metrics = transcript_records()[-1]["metrics"]
                self.assertEqual(typed, text)
                self.assertEqual(metrics["word_evidence_status"], "native")
                self.assertTrue(compiled)
                for voice in compiled:
                    # The fake audio really has the pause; only the switch
                    # decides whether the helper's words can act on it.
                    self.assertTrue(any(
                        event.kind == "pause" and event.duration >= 0.45
                        for event in voice.prosody))
                    native = [word for word in voice.hypotheses[0].words
                              if word.timing == "native"]
                    self.assertEqual(bool(native), compiled_words)
                    self.assertEqual(voice.pause_punctuation,
                                     prosody or not compiled_words)

    def test_switched_on_word_confidence_decides_the_review(self):
        mac = booted()
        spoken = "Send the invoice to Dana by Friday."
        mac.dictate_sentence(spoken, field=TextField())
        self.assertEqual(
            mac.dictate.PIPELINE_STATE["last_consequence_route"], "review")
        mac.dictate.PARAKEET_WORD_CONFIDENCE_ROUTING = True
        mac.desktop.word_confidence = 0.97
        try:
            mac.dictate_sentence(spoken, field=TextField())
            self.assertEqual(
                mac.dictate.PIPELINE_STATE["last_consequence_route"],
                "protected")
            mac.desktop.word_confidence = 0.4
            mac.dictate_sentence(spoken, field=TextField())
            self.assertEqual(
                mac.dictate.PIPELINE_STATE["last_consequence_route"],
                "review")
        finally:
            mac.desktop.word_confidence = 0.95


class SpeechRuntimeTests(unittest.TestCase):
    """Whisper Face's own runtime, behind PARROT_ASR_RUNTIME.

    Selected, the runtime is the first program asked (PARAKEET.preferred),
    with the FluidAudio helper behind it and Whisper behind that. The fake
    runtime hears exactly what the fake helper hears, so with the switch on
    every replayed sentence must type and route as origin/main did, and a
    runtime that is missing, does not start or fails a request must leave
    the typed text as the helper alone would have typed it.
    """

    SPOKEN = "Send the invoice to Dana by Friday."
    # Whisper mishears, so a fall all the way to Whisper changes the text.
    MISHEARD = "Send the invoice to Donna by Friday."

    def setUp(self):
        self.mac = booted()

    def tearDown(self):
        dictate = self.mac.dictate
        dictate.PARAKEET_RUNTIME = "fluidaudio"
        dictate.PARAKEET_RUNTIME_PINNED = False
        dictate.PARAKEET.preferred = None
        # Released first, so a slow start held by a test ends at once.
        for name in ("runtime_start_gate", "helper_start_gate"):
            gate = getattr(self.mac.desktop, name)
            if gate is not None:
                gate.set()
            setattr(self.mac.desktop, name, None)
        for client in (dictate.OWN_PARAKEET, dictate.PARAKEET):
            with client.lock:
                client._close()
            client.failed_starts = 0
            client.next_start = 0.0
        dictate.OWN_PARAKEET.missing_reported = False
        dictate.OWN_PARAKEET.start_allowance = (
            dictate.PARAKEET_RUNTIME_START_ALLOWANCE)
        self.mac.desktop.runtime_failure = None
        dictate.PARAKEET_RUNTIME_HELPER.write_bytes(b"")
        dictate.PARAKEET_RUNTIME_HELPER.chmod(0o755)

    def switch_on(self, *, wait=True):
        # What PARROT_ASR_RUNTIME=own sets up at import (tests/test_dictate.py,
        # SpeechRuntimeSwitchTests, holds the import-time expressions), then
        # what main() does with it at start-up: apply_speech_runtime, which
        # starts the runtime loading in the background. ``wait`` waits for
        # that load to end, ready or benched, as a person who turned the
        # switch on a while ago would find it.
        dictate = self.mac.dictate
        dictate.PARAKEET_RUNTIME = "own"
        dictate.PARAKEET_RUNTIME_PINNED = True
        dictate.PARAKEET._close()
        own = dictate.OWN_PARAKEET
        failed = own.failed_starts
        dictate.apply_speech_runtime()
        self.assertIs(dictate.PARAKEET.preferred, own)
        if not dictate.PARAKEET_RUNTIME_HELPER.is_file():
            return
        # The start is on a thread of its own: wait for it to begin, or,
        # with ``wait``, to end.
        if wait:
            self.wait_for_the_load(failed)
        else:
            self.assertTrue(self.mac.wait_until(
                lambda: own.loading is not None, 30.0), self.mac.log_tail())

    def wait_for_the_load(self, failed: int):
        """Until the runtime is ready, or benched once more than ``failed``."""
        own = self.mac.dictate.OWN_PARAKEET
        self.assertTrue(self.mac.wait_until(
            lambda: own.loading is None and (
                own.process is not None or own.failed_starts > failed),
            30.0), self.mac.log_tail())

    def hold_the_start(self, program="runtime"):
        """The runtime's (or the helper's) next start is slow until the
        test releases it."""
        gate = threading.Event()
        setattr(self.mac.desktop, f"{program}_start_gate", gate)
        self.addCleanup(gate.set)
        return gate

    def warm_up_on_a_thread(self):
        """main()'s warm-up, as it runs at app start, on its own thread."""
        errors = []

        def run():
            try:
                self.mac.dictate.warmup()
            except BaseException as error:  # reported by the test
                errors.append(error)

        thread = threading.Thread(target=run, name="test-warmup", daemon=True)
        thread.start()
        self.addCleanup(thread.join, 60)
        return thread, errors

    def wait_for_log(self, text, logged):
        self.assertTrue(self.mac.wait_until(
            lambda: text in self.mac.log.getvalue()[logged:], 30.0),
            self.mac.log_tail())

    def programs_spawned_since(self, count: int) -> list[str]:
        return [Path(argv[0]).name
                for argv in self.mac.desktop.helper_spawns[count:]]

    def dictate(self) -> str:
        return self.mac.dictate_sentence(
            self.SPOKEN, whisper=self.MISHEARD, field=TextField()).text

    def test_the_runtime_switch_ships_off(self):
        self.assertEqual(SHIPPED_RUNTIME,
                         {"runtime": "fluidaudio", "asked_first": None})

    def test_switched_off_the_runtime_is_never_started(self):
        desktop = self.mac.desktop
        spawns, asked = len(desktop.helper_spawns), len(desktop.runtime_requests)
        self.mac.dictate.PARAKEET._close()
        # What main() does at start-up, with the switch off.
        self.mac.dictate.apply_speech_runtime()
        self.assertIsNone(self.mac.dictate.OWN_PARAKEET.loading)
        for _ in range(3):
            self.assertEqual(self.dictate(), self.SPOKEN)
        self.assertEqual(len(desktop.runtime_requests), asked)
        self.assertIn(fake_platform.HELPER_NAME,
                      self.programs_spawned_since(spawns))
        self.assertNotIn(fake_platform.RUNTIME_NAME,
                         self.programs_spawned_since(spawns))

    def test_every_replayed_sentence_types_and_routes_as_origin_main(self):
        mac = self.mac
        desktop = mac.desktop
        self.switch_on()
        baseline = json.loads(BASELINE.read_text(encoding="utf-8"))["cases"]
        cases = [("default:" + case["id"], case) for case in load_corpus()]
        cases += [("replay:" + case["id"], case)
                  for case in recognizer_replay.load_cases()]
        self.assertGreaterEqual(
            len([key for key, _case in cases if key in baseline]), 197)
        helper_asked = len(desktop.parakeet_requests)
        runtime_asked = len(desktop.runtime_requests)
        for key, case in cases:
            result = HelperWordEvidenceTests.dictate_case(mac, key, case)
            with self.subTest(case=key):
                if key in baseline:
                    self.assertEqual(
                        (result["typed"], result["route"]),
                        (baseline[key]["typed"], baseline[key]["route"]))
                # The runtime answered, in protocol 2.
                self.assertEqual(result["status"], "native")
        self.assertGreaterEqual(
            len(desktop.runtime_requests) - runtime_asked, len(cases))
        # The FluidAudio helper was never needed.
        self.assertEqual(len(desktop.parakeet_requests), helper_asked)

    def test_the_window_switch_takes_effect_on_the_next_dictation(self):
        # The window's own "New speech runtime" action: the very next
        # dictation asks the runtime, with no restart, the choice reaches
        # preferences.json, and the status the window reads says which
        # program answered.
        dictate = self.mac.dictate
        desktop = self.mac.desktop
        actions = dictate.STATUS["bar"].gui.view_model.actions
        status_of = actions.status_snapshot
        self.addCleanup(dictate.PREFERENCES.__setitem__,
                        "new_speech_runtime", False)
        self.assertIs(dictate.PARAKEET_RUNTIME_PINNED, False)
        self.assertIs(status_of()["new_speech_runtime"], False)
        runtime_asked = len(desktop.runtime_requests)
        self.assertIs(actions.set_new_speech_runtime(True), True)
        self.assertIs(json.loads(dictate.PREFERENCES_FILE.read_text(
            encoding="utf-8"))["new_speech_runtime"], True)
        # It begins loading at once; once it is ready the next dictation
        # goes to it.
        self.assertTrue(self.mac.wait_until(
            lambda: dictate.OWN_PARAKEET.process is not None, 30.0),
            self.mac.log_tail())
        self.assertEqual(self.dictate(), self.SPOKEN)
        self.assertEqual(len(desktop.runtime_requests), runtime_asked + 1)
        status = status_of()
        self.assertEqual(
            (status["new_speech_runtime"], status["new_speech_runtime_last"]),
            (True, "new"))
        self.assertIs(actions.set_new_speech_runtime(False), False)
        helper_asked = len(desktop.parakeet_requests)
        self.assertEqual(self.dictate(), self.SPOKEN)
        self.assertEqual(len(desktop.runtime_requests), runtime_asked + 1)
        self.assertGreater(len(desktop.parakeet_requests), helper_asked)
        status = status_of()
        self.assertEqual(
            (status["new_speech_runtime"], status["new_speech_runtime_last"]),
            (False, "previous"))

    def test_a_missing_runtime_leaves_the_helper_to_answer(self):
        desktop = self.mac.desktop
        self.mac.dictate.PARAKEET_RUNTIME_HELPER.unlink()
        spawns, helper_asked = (len(desktop.helper_spawns),
                                len(desktop.parakeet_requests))
        self.switch_on()
        for _ in range(2):
            self.assertEqual(self.dictate(), self.SPOKEN)
        self.assertEqual(self.programs_spawned_since(spawns),
                         [fake_platform.HELPER_NAME])
        self.assertIsNone(self.mac.dictate.OWN_PARAKEET.loading)
        self.assertGreater(len(desktop.parakeet_requests), helper_asked)
        self.assertEqual(self.mac.log.getvalue().count(
            "! Speech runtime is not installed; using the FluidAudio helper"),
            1)

    def test_a_runtime_that_does_not_start_leaves_the_helper_to_answer(self):
        desktop = self.mac.desktop
        desktop.runtime_failure = "start"
        spawns, helper_asked = (len(desktop.helper_spawns),
                                len(desktop.parakeet_requests))
        self.switch_on()
        for _ in range(2):
            self.assertEqual(self.dictate(), self.SPOKEN)
        # Started once, at start-up, then benched: neither dictation waited
        # for it or started it again.
        self.assertEqual(self.programs_spawned_since(spawns),
                         [fake_platform.RUNTIME_NAME, fake_platform.HELPER_NAME])
        self.assertGreater(len(desktop.parakeet_requests), helper_asked)
        self.assertIn("! Speech runtime did not start (not ready); using the "
                      "FluidAudio helper", self.mac.log.getvalue())

    def test_a_runtime_that_fails_mid_use_hands_its_request_to_the_helper(self):
        desktop = self.mac.desktop
        self.switch_on()
        # Healthy first, so the failure comes from a runtime that was in use.
        self.assertEqual(self.dictate(), self.SPOKEN)
        process = self.mac.dictate.OWN_PARAKEET.process
        self.assertIsNotNone(process)
        desktop.runtime_failure = "request"
        runtime_asked = len(desktop.runtime_requests)
        helper_asked = len(desktop.parakeet_requests)
        self.assertEqual(self.dictate(), self.SPOKEN)
        # The request the runtime failed went to the helper, same audio.
        failed = desktop.runtime_requests[runtime_asked:]
        answered = desktop.parakeet_requests[helper_asked:]
        self.assertEqual(len(failed), 1)
        self.assertEqual(answered[:1], failed)
        # The failing process was closed, not left running.
        self.assertIsNone(self.mac.dictate.OWN_PARAKEET.process)
        self.assertIsNotNone(process.poll())
        self.assertIn("! Speech runtime failed; using the FluidAudio helper, "
                      "next attempt in 30s", self.mac.log.getvalue())
        # Benched: the next dictation goes straight to the helper.
        self.assertEqual(self.dictate(), self.SPOKEN)
        self.assertEqual(len(desktop.runtime_requests), runtime_asked + 1)

    def test_a_slow_start_is_answered_by_the_helper_then_taken_over(self):
        # A cold Core ML cache: the runtime takes longer to load than the
        # helper's start-up limit. It is not stopped while it loads; the
        # helper types meanwhile, and the runtime takes over once ready.
        dictate = self.mac.dictate
        desktop = self.mac.desktop
        own = dictate.OWN_PARAKEET
        status_of = dictate.STATUS["bar"].gui.view_model.actions.status_snapshot
        gate = self.hold_the_start()
        runtime_asked = len(desktop.runtime_requests)
        helper_asked = len(desktop.parakeet_requests)
        logged = len(self.mac.log.getvalue())
        self.switch_on(wait=False)
        loading = own.loading
        self.assertIsNotNone(loading)
        self.assertIs(status_of()["new_speech_runtime_preparing"], True)
        for _ in range(2):
            self.assertEqual(self.dictate(), self.SPOKEN)
        self.assertGreater(len(desktop.parakeet_requests), helper_asked)
        self.assertEqual(len(desktop.runtime_requests), runtime_asked)
        # Still loading, the same process, never stopped.
        self.assertIs(own.loading, loading)
        self.assertIsNone(loading.poll())
        gate.set()
        self.assertTrue(self.mac.wait_until(
            lambda: own.process is loading, 30.0), self.mac.log_tail())
        self.assertIs(status_of()["new_speech_runtime_preparing"], False)
        self.assertEqual(self.dictate(), self.SPOKEN)
        self.assertEqual(len(desktop.runtime_requests), runtime_asked + 1)
        # The helper let go once the runtime answered.
        self.assertIsNone(dictate.PARAKEET.process)
        self.assertNotIn("Speech runtime did not start",
                         self.mac.log.getvalue()[logged:])
        self.assertEqual(status_of()["new_speech_runtime_last"], "new")

    def test_a_dictation_while_it_loads_does_not_wait_for_it(self):
        # The start is held for the whole test. A dictation that waited for
        # it would not finish (dictate_sentence gives up after 10 s and
        # fails); it is typed by the helper while the load is still under
        # way.
        own = self.mac.dictate.OWN_PARAKEET
        desktop = self.mac.desktop
        gate = self.hold_the_start()
        self.switch_on(wait=False)
        loading = own.loading
        runtime_asked = len(desktop.runtime_requests)
        self.assertEqual(self.dictate(), self.SPOKEN)
        self.assertFalse(gate.is_set())
        self.assertIs(own.loading, loading)
        self.assertEqual(len(desktop.runtime_requests), runtime_asked)

    def test_a_start_that_never_finishes_is_stopped_and_benched(self):
        # The allowance is scaled down so the test does not wait a minute;
        # the start is never released.
        dictate = self.mac.dictate
        desktop = self.mac.desktop
        own = dictate.OWN_PARAKEET
        self.hold_the_start()
        own.start_allowance = 0.2
        spawns = len(desktop.helper_spawns)
        logged = len(self.mac.log.getvalue())
        self.switch_on(wait=False)
        loading = own.loading
        self.assertEqual(self.dictate(), self.SPOKEN)
        self.wait_for_the_load(0)
        self.assertIsNone(own.process)
        self.assertTrue(self.mac.wait_until(
            lambda: loading.poll() is not None, 30.0))
        self.assertIn("! Speech runtime did not start (TimeoutError); using "
                      "the FluidAudio helper, next attempt in 30s",
                      self.mac.log.getvalue()[logged:])
        # Benched: the next dictation is the helper's, and the runtime is
        # not started again.
        runtime_asked = len(desktop.runtime_requests)
        self.assertEqual(self.dictate(), self.SPOKEN)
        self.assertEqual(len(desktop.runtime_requests), runtime_asked)
        self.assertEqual(self.programs_spawned_since(spawns).count(
            fake_platform.RUNTIME_NAME), 1)
        self.assertIsNone(own.loading)

    def test_warmup_waits_for_the_runtime_and_does_not_start_the_helper(
            self):
        # A cold start: the runtime is still loading when warm-up reaches
        # its decode. Warm-up waits for the load, then that decode goes to
        # the runtime, and the FluidAudio helper is never started beside it.
        dictate = self.mac.dictate
        desktop = self.mac.desktop
        gate = self.hold_the_start()
        logged = len(self.mac.log.getvalue())
        self.switch_on(wait=False)
        spawns = len(desktop.helper_spawns)
        runtime_asked = len(desktop.runtime_requests)
        thread, errors = self.warm_up_on_a_thread()
        self.wait_for_log(
            "Waiting for the speech runtime to finish loading", logged)
        self.assertTrue(thread.is_alive())
        self.assertEqual(self.programs_spawned_since(spawns), [])
        gate.set()
        thread.join(60)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertGreater(len(desktop.runtime_requests), runtime_asked)
        self.assertEqual(self.programs_spawned_since(spawns), [])
        self.assertIsNone(dictate.PARAKEET.process)
        self.assertIsNone(dictate.PARAKEET.loading)

    def test_a_dictation_during_the_warmup_wait_is_typed_as_now(self):
        # The wait is outside the ASR pool: a dictation meanwhile is not
        # queued behind it, and is typed by the helper while the runtime
        # loads, as without the wait.
        desktop = self.mac.desktop
        gate = self.hold_the_start()
        logged = len(self.mac.log.getvalue())
        self.switch_on(wait=False)
        thread, errors = self.warm_up_on_a_thread()
        self.wait_for_log(
            "Waiting for the speech runtime to finish loading", logged)
        helper_asked = len(desktop.parakeet_requests)
        self.assertEqual(self.dictate(), self.SPOKEN)
        self.assertGreater(len(desktop.parakeet_requests), helper_asked)
        self.assertTrue(thread.is_alive())
        gate.set()
        thread.join(60)
        self.assertEqual(errors, [])

    def test_switched_off_warmup_does_not_wait(self):
        # With the switch off nothing is loading: warm-up runs straight
        # through, and its decode goes to the helper as it always has.
        logged = len(self.mac.log.getvalue())
        self.hold_the_start()   # a runtime start would hang; none happens
        thread, errors = self.warm_up_on_a_thread()
        thread.join(60)
        self.assertFalse(thread.is_alive())
        self.assertEqual(errors, [])
        self.assertNotIn("Waiting for the speech runtime",
                         self.mac.log.getvalue()[logged:])
        self.assertIsNone(self.mac.dictate.OWN_PARAKEET.loading)

    def test_a_helper_start_past_its_limit_goes_on_loading_then_answers(
            self):
        # The switch is off: the FluidAudio helper alone, as for every
        # user. Its limit is scaled down so the test waits no real 10 s; its
        # start is held past it. That dictation falls to Whisper as before,
        # but the helper is not stopped: once loaded it answers.
        dictate = self.mac.dictate
        desktop = self.mac.desktop
        helper = dictate.PARAKEET
        with helper.lock:
            helper._close()
        self.addCleanup(setattr, dictate, "PARAKEET_STARTUP_TIMEOUT",
                        dictate.PARAKEET_STARTUP_TIMEOUT)
        dictate.PARAKEET_STARTUP_TIMEOUT = 0.2
        gate = self.hold_the_start("helper")
        logged = len(self.mac.log.getvalue())
        spawns = len(desktop.helper_spawns)
        self.assertEqual(self.dictate(), self.MISHEARD)
        loading = helper.loading
        self.assertIsNotNone(loading)
        self.assertIsNone(loading.poll())
        # While it loads, Whisper answers at once and nothing restarts it.
        self.assertEqual(self.dictate(), self.MISHEARD)
        self.assertIs(helper.loading, loading)
        self.assertEqual(self.programs_spawned_since(spawns),
                         [fake_platform.HELPER_NAME])
        gate.set()
        self.assertTrue(self.mac.wait_until(
            lambda: helper.process is loading, 30.0), self.mac.log_tail())
        self.assertEqual(self.dictate(), self.SPOKEN)
        log = self.mac.log.getvalue()[logged:]
        self.assertIn("[asr] Parakeet helper is loading; Whisper answers "
                      "until it is ready", log)
        self.assertNotIn("Parakeet helper did not start", log)

    def test_when_both_fail_whisper_types_as_it_always_has(self):
        desktop = self.mac.desktop
        self.switch_on()
        desktop.runtime_failure = "request"
        desktop.helper_failure = "request"
        try:
            self.assertEqual(self.dictate(), self.MISHEARD)
        finally:
            desktop.helper_failure = None
        self.assertIn("! Parakeet helper failed; using Whisper Turbo",
                      self.mac.log.getvalue())


if __name__ == "__main__":
    unittest.main()
