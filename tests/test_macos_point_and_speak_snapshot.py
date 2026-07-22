# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import json
from pathlib import Path
from types import SimpleNamespace
import sys
import unittest


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from macos_point_and_speak_snapshot import (  # noqa: E402
    SnapshotState,
    SystemMacAccessibilityReader,
    capture_accessibility_targets,
)
from point_and_speak_resolver import (  # noqa: E402
    ResolutionState,
    TargetSnapshot,
    resolve_point_and_speak,
)


class FakeReader:
    def __init__(self, nodes, *, trusted=True, root="root"):
        self.nodes = nodes
        self.is_trusted = trusted
        self.root_value = root
        self.requested = []

    def trusted(self):
        return self.is_trusted

    def root(self):
        return self.root_value

    def attribute(self, element, name):
        self.requested.append(name)
        return self.nodes.get(element, {}).get(name)

    def geometry(self, element):
        self.requested.append("geometry")
        return self.nodes.get(element, {}).get("geometry")


class FakeApplicationServices:
    def __init__(self):
        self.requests = []

    def AXIsProcessTrusted(self):
        return True

    def AXUIElementCreateSystemWide(self):
        return "system-wide"

    def AXUIElementCopyAttributeValue(self, element, attribute, _parameter):
        self.requests.append((element, attribute))
        values = {
            ("system-wide", "AXFocusedApplication"): "frontmost-app",
            ("frontmost-app", "AXRole"): "AXButton",
            ("frontmost-app", "AXPosition"): "position",
            ("frontmost-app", "AXSize"): "size",
        }
        value = values.get((element, attribute))
        return (0, value) if value is not None else (1, None)

    def AXValueGetValue(self, value, value_type, _output):
        if value == "position" and value_type == 1:
            return True, SimpleNamespace(x=12, y=24)
        if value == "size" and value_type == 2:
            return True, SimpleNamespace(width=80, height=30)
        return False, None


def tree():
    return {
        "root": {"children": ["save", "search", "private"]},
        "save": {
            "role": "AXButton", "title": "Save Changes", "description": "",
            "geometry": (10, 10, 100, 30), "enabled": True,
            "focused": False, "hidden": False,
        },
        "search": {
            "role": "AXTextField", "title": "", "description": "Search",
            "geometry": (150, 10, 200, 30), "enabled": True,
            "focused": True, "hidden": False,
        },
        "private": {
            "role": "AXTextArea", "title": "", "description": "",
            "value": "Project Bluebird budget 8492",
            "geometry": (10, 80, 300, 200), "enabled": True,
            "focused": False, "hidden": False,
        },
    }


class MacPointAndSpeakSnapshotTests(unittest.TestCase):
    def test_capture_projects_only_closed_read_only_resolver_fields(self):
        reader = FakeReader(tree())

        capture = capture_accessibility_targets(reader)

        self.assertEqual(capture.receipt.state, SnapshotState.CAPTURED)
        self.assertEqual(capture.receipt.emitted_targets, 2)
        self.assertEqual(capture.receipt.skipped_elements, 1)
        self.assertNotIn("value", reader.requested)
        for raw in capture.targets:
            target = TargetSnapshot.from_mapping(raw)
            self.assertFalse(hasattr(target, "click"))
        resolved = resolve_point_and_speak("Save Changes", capture.targets)
        self.assertEqual(resolved.state, ResolutionState.RESOLVED)
        self.assertEqual(resolved.target_id, "ax-0000")

    def test_private_values_never_enter_targets_or_receipts(self):
        secret = tree()["private"]["value"]
        capture = capture_accessibility_targets(FakeReader(tree()))
        serialized = json.dumps({
            "targets": list(capture.targets),
            "receipt": {
                "state": capture.receipt.state.value,
                "observed": capture.receipt.observed_elements,
            },
        })

        self.assertNotIn(secret, serialized)
        self.assertNotIn("document_text", serialized)
        self.assertNotIn("selected_text", serialized)

    def test_permission_and_root_failures_are_content_free(self):
        denied = capture_accessibility_targets(
            FakeReader({}, trusted=False))
        unavailable = capture_accessibility_targets(
            FakeReader({}, root=None))

        self.assertEqual(denied.receipt.state, SnapshotState.PERMISSION_DENIED)
        self.assertEqual(unavailable.receipt.state, SnapshotState.UNAVAILABLE)
        self.assertEqual(denied.targets, ())
        self.assertEqual(unavailable.targets, ())

    def test_malformed_names_geometry_and_disabled_targets_fail_closed(self):
        nodes = tree()
        nodes["save"]["title"] = "x" * 129
        nodes["search"]["geometry"] = (0, 0, float("nan"), 20)
        capture = capture_accessibility_targets(FakeReader(nodes))

        self.assertEqual(capture.targets, ())
        self.assertEqual(capture.receipt.skipped_elements, 3)

    def test_capture_is_bounded_and_marks_truncation(self):
        nodes = {"root": {"children": [f"n{i}" for i in range(300)]}}
        for index in range(300):
            nodes[f"n{index}"] = {
                "role": "AXButton", "title": f"Button {index}",
                "geometry": (index, 0, 1, 1), "enabled": True,
                "hidden": False,
            }

        capture = capture_accessibility_targets(FakeReader(nodes))

        self.assertEqual(len(capture.targets), 256)
        self.assertTrue(capture.receipt.truncated)

    def test_cycles_and_malformed_reader_values_do_not_escape_or_emit(self):
        nodes = {
            "root": {"children": ["root", "bad"]},
            "bad": {
                "role": "AXButton", "title": "Danger",
                "geometry": object(), "hidden": "unknown", "enabled": True,
            },
        }
        capture = capture_accessibility_targets(FakeReader(nodes))

        self.assertEqual(capture.targets, ())
        self.assertEqual(capture.receipt.observed_elements, 2)
        self.assertGreaterEqual(capture.receipt.skipped_elements, 2)
        self.assertFalse(capture.receipt.truncated)

    def test_concrete_reader_uses_only_read_only_application_services_calls(self):
        services = FakeApplicationServices()
        reader = object.__new__(SystemMacAccessibilityReader)
        reader._services = services

        self.assertTrue(reader.trusted())
        app = reader.root()
        self.assertEqual(app, "frontmost-app")
        self.assertEqual(reader.attribute(app, "role"), "AXButton")
        self.assertEqual(reader.geometry(app), (12.0, 24.0, 80.0, 30.0))
        with self.assertRaises(ValueError):
            reader.attribute(app, "value")

        requested_attributes = {attribute for _, attribute in services.requests}
        self.assertEqual(requested_attributes, {
            "AXFocusedApplication", "AXRole", "AXPosition", "AXSize",
        })
        forbidden = {"AXValue", "AXDocument", "AXSelectedText", "AXAction"}
        self.assertFalse(requested_attributes & forbidden)


if __name__ == "__main__":
    unittest.main()
