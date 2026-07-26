# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import ast
import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest import mock

ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from delayed_cleanup_merge import (  # noqa: E402
    DelayedApplyOutcome,
    DelayedCleanupTransactionAdapter,
    DestinationSnapshot,
)
from macos_delayed_cleanup_destination import (  # noqa: E402
    DestinationCaptureState,
    MacDestinationStateAdapter,
    SystemMacDestinationStateReader,
    capture_frontmost_destination_state,
)


def observation(**changes):
    value = {
        "schema_version": 1,
        "pid": 501,
        "window_id": 44,
        "element_id": "message-editor",
        "role": "AXTextArea",
        "text": "We should uh ship Friday.",
        "selection": (25, 0),
        "focused": True,
        "enabled": True,
    }
    value.update(changes)
    return value


class FakeReader:
    def __init__(self, values, *, trusted=True):
        self.values = iter(values)
        self.is_trusted = trusted

    def trusted(self):
        return self.is_trusted

    def read_focused_destination(self):
        return next(self.values)

    def compare_and_swap_focused_destination(self, expected, replacement):
        current = next(self.values)
        return current == expected and isinstance(replacement, str)


class FakeServices:
    def __init__(self, *, selected="selected-range", extracted=None):
        self.requests = []
        self.writes = []
        self.selected = selected
        self.value = "Private destination words"
        self.extracted = extracted if extracted is not None else (
            True, SimpleNamespace(location=25, length=0))

    def AXIsProcessTrusted(self):
        return True

    def AXUIElementCreateSystemWide(self):
        return "system"

    def AXUIElementCopyAttributeValue(self, element, attribute, _parameter):
        self.requests.append((element, attribute))
        values = {
            ("system", "AXFocusedApplication"): "app",
            ("app", "AXFocusedWindow"): "window",
            ("app", "AXFocusedUIElement"): "editor",
            ("window", "AXWindowNumber"): 44,
            ("editor", "AXIdentifier"): "message-editor",
            ("editor", "AXRole"): "AXTextArea",
            ("editor", "AXValue"): self.value,
            ("editor", "AXSelectedTextRange"): self.selected,
            ("editor", "AXFocused"): True,
            ("editor", "AXEnabled"): True,
        }
        value = values.get((element, attribute))
        return (0, value) if value is not None else (1, None)

    def AXUIElementGetPid(self, element, _output):
        return (0, 501) if element == "app" else (1, None)

    def AXValueGetValue(self, value, kind, _output):
        if value == "selected-range" and kind == 4:
            return self.extracted
        return False, None

    def AXUIElementSetAttributeValue(self, element, attribute, value):
        self.writes.append((element, attribute, value))
        if element != "editor" or attribute != "AXValue":
            return 1
        self.value = value
        return 0


class MacDelayedCleanupDestinationTests(unittest.TestCase):
    def capture(self, *values, trusted=True):
        adapter = MacDestinationStateAdapter(
            FakeReader(values, trusted=trusted), token_key=b"k" * 32)
        with mock.patch(
                "macos_delayed_cleanup_destination.sys.platform", "darwin"):
            return adapter, tuple(adapter.capture() for _ in values)

    def test_projects_exact_snapshot_with_opaque_stable_tokens(self):
        adapter, captures = self.capture(observation(), observation())
        first, second = captures

        self.assertEqual(first.receipt.state, DestinationCaptureState.CAPTURED)
        self.assertIsInstance(first.snapshot, DestinationSnapshot)
        self.assertEqual(first.snapshot.text, "We should uh ship Friday.")
        self.assertEqual(first.selection, (25, 0))
        self.assertEqual(first.snapshot.destination_id,
                         second.snapshot.destination_id)
        self.assertEqual(first.snapshot.revision, second.snapshot.revision)
        self.assertTrue(first.snapshot.focused)
        self.assertNotIn("message-editor", first.snapshot.destination_id)
        self.assertNotIn("Friday", first.snapshot.revision)
        self.assertIsNotNone(adapter)

    def test_text_selection_and_identity_drift_change_only_expected_tokens(self):
        _, captures = self.capture(
            observation(),
            observation(text="User changed it", selection=(15, 0)),
            observation(element_id="other-editor"),
        )
        original, revised, other = captures

        self.assertEqual(original.snapshot.destination_id,
                         revised.snapshot.destination_id)
        self.assertNotEqual(original.snapshot.revision,
                            revised.snapshot.revision)
        self.assertNotEqual(original.snapshot.destination_id,
                            other.snapshot.destination_id)

    def test_snapshot_plugs_into_transaction_and_applies_after_final_cas(self):
        reader = FakeReader((
            observation(), observation(), observation(),
        ))
        destination = MacDestinationStateAdapter(
            reader, token_key=b"k" * 32)

        with mock.patch(
                "macos_delayed_cleanup_destination.sys.platform", "darwin"):
            receipt = DelayedCleanupTransactionAdapter().apply(
                "proposal-1",
                "We should uh ship Friday.",
                "We should ship Friday.",
                lambda: destination.capture().snapshot,
                destination.apply_if_unchanged,
            )

        self.assertEqual(receipt.outcome, DelayedApplyOutcome.APPLIED)
        self.assertTrue(receipt.applied)

    def test_final_cas_rejects_drift_and_never_retries_a_consumed_revision(self):
        reader = FakeReader((
            observation(), observation(text="User typed"),
        ))
        destination = MacDestinationStateAdapter(
            reader, token_key=b"k" * 32)
        with mock.patch(
                "macos_delayed_cleanup_destination.sys.platform", "darwin"):
            expected = destination.capture().snapshot
            self.assertIsNotNone(expected)
            self.assertFalse(destination.apply_if_unchanged(
                expected, "We should ship Friday."))
            self.assertFalse(destination.apply_if_unchanged(
                expected, "We should ship Friday."))

    def test_receipt_and_repr_never_expose_destination_content_or_ids(self):
        _, (capture,) = self.capture(observation(
            text="Project Bluebird private destination"))

        encoded = json.dumps({
            "state": capture.receipt.state.value,
            "focused": capture.receipt.focused,
            "enabled": capture.receipt.enabled,
            "selection_present": capture.receipt.selection_present,
            "identity_complete": capture.receipt.identity_complete,
        })
        self.assertNotIn("Bluebird", encoded)
        self.assertNotIn("message-editor", encoded)
        self.assertNotIn("Bluebird", repr(capture))
        self.assertNotIn("Bluebird", repr(capture.snapshot))

    def test_schema_expansion_private_fields_and_malformed_values_fail_closed(self):
        private = observation(selected_text="private selection")
        cases = (
            (private, DestinationCaptureState.PRIVATE_DATA_REJECTED),
            (observation(text="x" * 32_769), DestinationCaptureState.MALFORMED),
            (observation(selection=(999, 0)), DestinationCaptureState.MALFORMED),
            (observation(role="AXButton"), DestinationCaptureState.MALFORMED),
            (observation(element_id=""), DestinationCaptureState.MALFORMED),
            (observation(pid="501"), DestinationCaptureState.MALFORMED),
            (observation(text="private\x00tail"),
             DestinationCaptureState.MALFORMED),
            ({"schema_version": 1}, DestinationCaptureState.MALFORMED),
        )
        for index, (value, state) in enumerate(cases):
            with self.subTest(index=index, state=state):
                _, (capture,) = self.capture(value)
                self.assertEqual(capture.receipt.state, state)
                self.assertIsNone(capture.snapshot)

    def test_unfocused_disabled_permission_and_non_mac_are_unavailable(self):
        for value in (
            observation(focused=False), observation(enabled=False), None,
        ):
            with self.subTest(value=value is None):
                _, (capture,) = self.capture(value)
                self.assertEqual(
                    capture.receipt.state,
                    DestinationCaptureState.UNAVAILABLE)
                self.assertIsNone(capture.snapshot)
        _, (denied,) = self.capture(observation(), trusted=False)
        self.assertEqual(
            denied.receipt.state, DestinationCaptureState.PERMISSION_DENIED)
        with mock.patch(
                "macos_delayed_cleanup_destination.sys.platform", "linux"):
            capture = capture_frontmost_destination_state()
        self.assertEqual(capture.receipt.state,
                         DestinationCaptureState.UNAVAILABLE)
        self.assertIsNone(capture.snapshot)

    def test_concrete_reader_uses_copy_only_allowlist_and_no_private_metadata(self):
        services = FakeServices()
        reader = object.__new__(SystemMacDestinationStateReader)
        reader._services = services

        self.assertTrue(reader.trusted())
        raw = reader.read_focused_destination()

        self.assertEqual(set(raw), {
            "schema_version", "pid", "window_id", "element_id", "role",
            "text", "selection", "focused", "enabled",
        })
        requested = {attribute for _, attribute in services.requests}
        self.assertEqual(requested, {
            "AXFocusedApplication", "AXFocusedWindow", "AXFocusedUIElement",
            "AXWindowNumber", "AXIdentifier", "AXRole", "AXValue",
            "AXSelectedTextRange", "AXFocused", "AXEnabled",
        })
        self.assertFalse(requested & {
            "AXTitle", "AXDescription", "AXDocument", "AXSelectedText",
            "AXURL", "AXPath", "AXAction",
        })

    def test_concrete_reader_accepts_bounded_pyobjc_range_representations(self):
        variants = (
            ((25, 0), None),
            (SimpleNamespace(location=25, length=0), None),
            (SimpleNamespace(loc=25, len=0), None),
            ("selected-range", (True, (25, 0))),
            ("selected-range", (True, SimpleNamespace(
                location=25, length=0))),
        )
        for selected, extracted in variants:
            with self.subTest(selected=type(selected).__name__):
                services = FakeServices(
                    selected=selected, extracted=extracted)
                reader = object.__new__(SystemMacDestinationStateReader)
                reader._services = services
                raw = reader.read_focused_destination()
                self.assertEqual(raw["selection"], (25, 0))
                requested = {attribute for _, attribute in services.requests}
                self.assertNotIn("AXSelectedText", requested)

        services = FakeServices(
            selected="selected-range", extracted=(False, (25, 0)))
        reader = object.__new__(SystemMacDestinationStateReader)
        reader._services = services
        self.assertIsNone(reader.read_focused_destination())
        requested = {attribute for _, attribute in services.requests}
        self.assertNotIn("AXSelectedText", requested)

    def test_concrete_reader_sets_value_only_after_exact_observation_recheck(self):
        services = FakeServices()
        reader = object.__new__(SystemMacDestinationStateReader)
        reader._services = services
        expected = reader.read_focused_destination()

        self.assertTrue(reader.compare_and_swap_focused_destination(
            expected, "Clean destination words"))
        self.assertEqual(
            services.writes,
            [("editor", "AXValue", "Clean destination words")],
        )

        stale = dict(expected)
        stale["text"] = "Stale private words"
        self.assertFalse(reader.compare_and_swap_focused_destination(
            stale, "Must not write"))
        self.assertEqual(len(services.writes), 1)

    def test_module_has_only_allowlisted_ax_write_and_no_other_side_effects(self):
        tree = ast.parse((ROOT / "macos_delayed_cleanup_destination.py").read_text(
            encoding="utf-8"))
        called_attributes = {
            node.func.attr for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
        }
        called_names = {
            node.func.id for node in ast.walk(tree)
            if isinstance(node, ast.Call) and isinstance(node.func, ast.Name)
        }
        self.assertFalse(called_attributes & {
            "AXUIElementPerformAction", "setString", "write", "send",
            "connect", "setValue", "paste", "type",
        })
        self.assertEqual(
            sum(name == "AXUIElementSetAttributeValue"
                for name in called_attributes),
            1,
        )
        self.assertFalse(called_names & {
            "open", "print", "exec", "eval", "compile", "paste_text",
            "type_text", "click", "focus", "drag", "drop",
        })


if __name__ == "__main__":
    unittest.main()
