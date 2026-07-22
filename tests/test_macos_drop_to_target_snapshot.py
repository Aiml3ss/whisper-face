# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import json
import sys
import unittest
from pathlib import Path
from types import SimpleNamespace


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from drop_to_target import DecisionState, decide_drop_to_target  # noqa: E402
from macos_drop_to_target_snapshot import (  # noqa: E402
    DropCapability,
    SnapshotState,
    SystemMacAccessibilityReader,
    capture_drop_target_evidence,
)
from drop_to_target import DropEffect, SourceKind  # noqa: E402


POLICY = {
    "AXGroup": DropCapability((SourceKind.FILE_REFERENCE,), (DropEffect.COPY,)),
}


class FakeReader:
    def __init__(self, nodes, *, trusted=True, root="root"):
        self.nodes, self.is_trusted, self.root_value = nodes, trusted, root
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


def tree():
    return {
        "root": {"children": ["inbox", "private", "button"]},
        "inbox": {"role": "AXGroup", "title": "Team Inbox", "description": "",
                  "geometry": (12, 24, 180, 60), "hidden": False,
                  "enabled": True, "drop_enabled": True},
        "private": {"role": "AXTextArea", "value": "Secret plan 8492",
                    "selected_text": "Secret plan", "geometry": (1, 2, 3, 4)},
        "button": {"role": "AXButton", "title": "Upload", "geometry": (1, 2, 3, 4),
                   "hidden": False, "enabled": True, "drop_enabled": True},
    }


class FakeApplicationServices:
    def __init__(self):
        self.requests = []

    def AXIsProcessTrusted(self):
        return True

    def AXUIElementCreateSystemWide(self):
        return "system"

    def AXUIElementCopyAttributeValue(self, element, attribute, _parameter):
        self.requests.append((element, attribute))
        values = {("system", "AXFocusedApplication"): "app", ("app", "AXRole"): "AXGroup",
                  ("app", "AXPosition"): "position", ("app", "AXSize"): "size"}
        value = values.get((element, attribute))
        return (0, value) if value is not None else (1, None)

    def AXValueGetValue(self, value, kind, _output):
        if value == "position" and kind == 1:
            return True, SimpleNamespace(x=12, y=24)
        if value == "size" and kind == 2:
            return True, SimpleNamespace(width=80, height=30)
        return False, None


class MacDropTargetSnapshotTests(unittest.TestCase):
    def test_projects_only_closed_resolver_facts_and_never_reads_private_content(self):
        reader = FakeReader(tree())
        capture = capture_drop_target_evidence(reader, POLICY)

        self.assertEqual(capture.receipt.state, SnapshotState.CAPTURED)
        self.assertEqual(capture.receipt.emitted_targets, 1)
        self.assertNotIn("value", reader.requested)
        self.assertNotIn("selected_text", reader.requested)
        result = decide_drop_to_target({"schema_version": 1, "target_hint": "Team Inbox",
                                        "source_kind": "file_reference", "effect": "copy"},
                                       capture.targets)
        self.assertEqual((result.state, result.target_id),
                         (DecisionState.RESOLVED, "ax-drop-0000"))

    def test_receipt_and_targets_exclude_private_content_and_geometry(self):
        capture = capture_drop_target_evidence(FakeReader(tree()), POLICY)
        serialized = json.dumps({"targets": list(capture.targets),
                                 "receipt": capture.receipt.__dict__ if hasattr(capture.receipt, "__dict__") else str(capture.receipt)})
        self.assertNotIn("Secret plan 8492", serialized)
        self.assertNotIn("selected_text", serialized)
        self.assertNotIn("geometry", serialized)

    def test_unknown_role_or_missing_drop_capability_fails_closed(self):
        no_policy = capture_drop_target_evidence(FakeReader(tree()), {})
        missing_capability = tree()
        missing_capability["inbox"].pop("drop_enabled")
        malformed = capture_drop_target_evidence(FakeReader(missing_capability), POLICY)
        self.assertEqual(no_policy.targets, ())
        self.assertEqual(malformed.targets, ())
        self.assertEqual(malformed.receipt.skipped_elements, 1)

    def test_permission_failures_and_invalid_policy_are_content_free(self):
        denied = capture_drop_target_evidence(FakeReader({}, trusted=False), POLICY)
        unavailable = capture_drop_target_evidence(FakeReader({}, root=None), POLICY)
        self.assertEqual(denied.receipt.state, SnapshotState.PERMISSION_DENIED)
        self.assertEqual(unavailable.receipt.state, SnapshotState.UNAVAILABLE)
        with self.assertRaisesRegex(ValueError, "policy"):
            capture_drop_target_evidence(FakeReader({}), {"AXGroup": object()})

    def test_bounded_traversal_marks_truncation(self):
        nodes = {"root": {"children": [f"n{i}" for i in range(200)]}}
        for index in range(200):
            nodes[f"n{index}"] = {"role": "AXGroup", "title": f"Target {index}",
                                    "geometry": (index, 0, 1, 1), "hidden": False,
                                    "enabled": True, "drop_enabled": True}
        capture = capture_drop_target_evidence(FakeReader(nodes), POLICY)
        self.assertEqual(len(capture.targets), 128)
        self.assertTrue(capture.receipt.truncated)

    def test_concrete_reader_exposes_only_copy_attribute_reads(self):
        services = FakeApplicationServices()
        reader = object.__new__(SystemMacAccessibilityReader)
        reader._services = services
        self.assertTrue(reader.trusted())
        app = reader.root()
        self.assertEqual(reader.attribute(app, "role"), "AXGroup")
        self.assertEqual(reader.geometry(app), (12.0, 24.0, 80.0, 30.0))
        with self.assertRaises(ValueError):
            reader.attribute(app, "value")
        requested = {attribute for _, attribute in services.requests}
        self.assertEqual(requested, {"AXFocusedApplication", "AXRole", "AXPosition", "AXSize"})
        self.assertFalse(requested & {"AXValue", "AXDocument", "AXSelectedText", "AXAction"})


if __name__ == "__main__":
    unittest.main()
