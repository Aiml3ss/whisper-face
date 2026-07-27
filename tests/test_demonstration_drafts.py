import ast
import json
import os
import sys
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from demonstration_drafts import (  # noqa: E402
    DemonstrationAction,
    DemonstrationConflictError,
    DemonstrationDomain,
    DemonstrationDraftStore,
    DemonstrationFormatError,
    DemonstrationNotFoundError,
    DemonstrationState,
    DemonstrationTransitionError,
    MAX_STEP_TEXT_CHARS,
    MAX_STEPS,
)


DICTATE_TREE = ast.parse((ROOT / "dictate.py").read_text(encoding="utf-8"))


def load_runtime_definitions(*names, extra=None):
    selected = [
        node for node in DICTATE_TREE.body
        if isinstance(node, ast.FunctionDef) and node.name in names
    ]
    if {node.name for node in selected} != set(names):
        raise AssertionError("production demonstration runtime definitions missing")
    future_annotations = ast.ImportFrom(
        module="__future__", names=[ast.alias(name="annotations")], level=0)
    module = ast.fix_missing_locations(ast.Module(
        body=[future_annotations, *selected], type_ignores=[]))
    namespace = dict(extra or {})
    exec(compile(module, "dictate-demonstration-selected", "exec"), namespace)
    return namespace


def demo_id(number: int) -> str:
    return f"demo-{number:032x}"


class DemonstrationDraftStoreTests(unittest.TestCase):
    def setUp(self):
        self.temporary_directory = tempfile.TemporaryDirectory()
        self.addCleanup(self.temporary_directory.cleanup)
        self.path = Path(self.temporary_directory.name) / "private" / "demonstrations.json"
        self.store = DemonstrationDraftStore(self.path)

    def test_each_initial_domain_records_and_previews_only_local_steps(self):
        cases = (
            (DemonstrationDomain.FINDER, DemonstrationAction.CREATE_FOLDER, "Receipts"),
            (DemonstrationDomain.MAIL, DemonstrationAction.SET_SUBJECT, "Shipping update"),
            (DemonstrationDomain.NOTES, DemonstrationAction.SET_NOTE_BODY, "Private note body"),
            (DemonstrationDomain.MENU, DemonstrationAction.CHOOSE_MENU_ITEM, "File > New"),
        )
        for index, (domain, action, text) in enumerate(cases):
            with self.subTest(domain=domain):
                draft_id = demo_id(index + 1)
                started = self.store.begin(draft_id, domain)
                recorded = self.store.record(draft_id, action, text)
                preview = self.store.preview(draft_id)
                self.assertEqual(started.state, DemonstrationState.RECORDING)
                self.assertEqual(recorded.step_count, 1)
                self.assertEqual(preview.domain, domain)
                self.assertEqual(preview.steps[0].text, text)
                self.assertEqual(preview.state, DemonstrationState.RECORDING)
        if os.name != "nt":
            if os.name == "posix":
                self.assertEqual(
                    self.path.stat().st_mode & 0o777, 0o600)

    def test_receipts_and_reprs_do_not_leak_private_step_text(self):
        secret = "Unreleased acquisition plan for Casey"
        draft_id = demo_id(10)
        self.store.begin(draft_id, DemonstrationDomain.MAIL)
        receipt = self.store.record(draft_id, DemonstrationAction.SET_BODY, secret)
        draft = self.store.preview(draft_id)

        encoded = json.dumps(receipt.to_dict(), sort_keys=True)
        self.assertEqual(set(receipt.to_dict()), {"schema_version", "kind", "domain", "state", "sequence", "step_count"})
        self.assertNotIn(secret, encoded)
        self.assertNotIn(draft_id, encoded)
        self.assertNotIn(secret, repr(draft))
        self.assertNotIn(draft_id, repr(draft))
        self.assertNotIn(secret, repr(draft.steps[0]))

    def test_cancel_rolls_back_recording_text_and_approval_stays_inert_and_durable(self):
        cancel_id = demo_id(20)
        self.store.begin(cancel_id, DemonstrationDomain.NOTES)
        self.store.record(cancel_id, DemonstrationAction.SET_NOTE_BODY, "Erase this private text")
        cancelled = self.store.cancel(cancel_id)
        self.assertEqual(cancelled.state, DemonstrationState.CANCELLED)
        with self.assertRaises(DemonstrationNotFoundError):
            self.store.preview(cancel_id)
        self.assertNotIn("Erase this private text", self.path.read_text(encoding="utf-8"))

        approve_id = demo_id(21)
        self.store.begin(approve_id, DemonstrationDomain.FINDER)
        self.store.record(approve_id, DemonstrationAction.SELECT_ITEM, "Quarterly plan")
        approved = self.store.approve(approve_id)
        restored = DemonstrationDraftStore(self.path)
        self.assertEqual(approved.state, DemonstrationState.APPROVED)
        self.assertEqual(restored.preview(approve_id).state, DemonstrationState.APPROVED)
        with self.assertRaises(DemonstrationTransitionError):
            restored.record(approve_id, DemonstrationAction.RENAME_ITEM, "Never execute")
        with self.assertRaises(DemonstrationTransitionError):
            restored.cancel(approve_id)
        deleted = restored.delete_approved(approve_id)
        self.assertEqual(deleted.state, DemonstrationState.APPROVED)
        with self.assertRaises(DemonstrationNotFoundError):
            restored.preview(approve_id)
        self.assertNotIn(
            "Quarterly plan", self.path.read_text(encoding="utf-8"))

    def test_rejects_cross_domain_actions_and_bounded_or_invalid_inputs(self):
        draft_id = demo_id(30)
        self.store.begin(draft_id, DemonstrationDomain.MENU)
        with self.assertRaisesRegex(ValueError, "not allowed"):
            self.store.record(draft_id, DemonstrationAction.SET_BODY, "wrong domain")
        with self.assertRaises(ValueError):
            self.store.record(draft_id, DemonstrationAction.OPEN_MENU, "x" * (MAX_STEP_TEXT_CHARS + 1))
        for index in range(MAX_STEPS):
            self.store.record(draft_id, DemonstrationAction.OPEN_MENU, f"Menu {index}")
        with self.assertRaisesRegex(OverflowError, "limit"):
            self.store.record(draft_id, DemonstrationAction.OPEN_MENU, "One too many")
        with self.assertRaises(ValueError):
            self.store.begin("not an opaque id", DemonstrationDomain.MENU)
        for invalid_id in ("demo:mnemonic", "demo-private", "demo-ABCDEF0123456789abcdef0123456789", demo_id(1) + "0"):
            with self.subTest(invalid_id=invalid_id):
                with self.assertRaisesRegex(ValueError, "opaque"):
                    self.store.begin(invalid_id, DemonstrationDomain.MENU)
        with self.assertRaises(ValueError):
            self.store.begin(demo_id(31), "menu")

    def test_closed_schema_and_id_conflicts_fail_closed(self):
        draft_id = demo_id(40)
        self.store.begin(draft_id, DemonstrationDomain.MAIL)
        self.assertEqual(self.store.begin(draft_id, DemonstrationDomain.MAIL).sequence, 1)
        with self.assertRaises(DemonstrationConflictError):
            self.store.begin(draft_id, DemonstrationDomain.NOTES)
        body = {"schema_version": 1, "kind": "whisper-face/demonstration-drafts", "next_sequence": 2, "drafts": [{"draft_id": demo_id(41), "domain": "mail", "state": "recording", "sequence": 1, "steps": [], "extra": "private"}]}
        self.path.write_text(json.dumps(body), encoding="utf-8")
        with self.assertRaises(DemonstrationFormatError):
            DemonstrationDraftStore(self.path)

    def test_failed_atomic_replace_preserves_disk_and_memory(self):
        draft_id = demo_id(50)
        self.store.begin(draft_id, DemonstrationDomain.NOTES)
        before = self.path.read_bytes()
        with patch("demonstration_drafts.os.replace", side_effect=OSError("failure")):
            with self.assertRaises(OSError):
                self.store.record(draft_id, DemonstrationAction.CREATE_NOTE, "do not retain")
        self.assertEqual(self.path.read_bytes(), before)
        self.assertEqual(self.store.preview(draft_id).steps, ())
        self.assertEqual(DemonstrationDraftStore(self.path).preview(draft_id).steps, ())
        self.assertEqual(list(self.path.parent.glob("*.tmp")), [])

    def test_empty_drafts_cannot_be_approved(self):
        draft_id = demo_id(60)
        self.store.begin(draft_id, DemonstrationDomain.NOTES)
        with self.assertRaisesRegex(DemonstrationTransitionError, "empty"):
            self.store.approve(draft_id)
        with self.assertRaisesRegex(DemonstrationTransitionError, "approved"):
            self.store.delete_approved(draft_id)


class DemonstrationDraftRuntimeTests(unittest.TestCase):
    @staticmethod
    def namespace(path: Path, *, is_macos: bool = True):
        state = {"lock": __import__("threading").RLock(), "store": None}
        return load_runtime_definitions(
            "_demonstration_draft_store",
            "_demonstration_metadata",
            "inspect_demonstration_drafts",
            "create_demonstration_draft",
            "reveal_demonstration_draft",
            "record_demonstration_step",
            "approve_demonstration_draft",
            "cancel_demonstration_draft",
            "delete_approved_demonstration_draft",
            extra={
                "IS_MACOS": is_macos,
                "DEMONSTRATION_DRAFTS_FILE": path,
                "DEMONSTRATION_DRAFTS_STATE": state,
                "DemonstrationAction": DemonstrationAction,
                "DemonstrationDomain": DemonstrationDomain,
                "DemonstrationDraftStore": DemonstrationDraftStore,
                "os": SimpleNamespace(urandom=lambda size: b"\x2a" * size),
            },
        )

    def test_runtime_allocates_opaque_ids_and_keeps_listing_content_free(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demonstrations.json"
            ns = self.namespace(path)

            created = ns["create_demonstration_draft"]("mail")
            self.assertEqual(created["draft_id"], "demo-" + "2a" * 16)
            self.assertEqual(created["step_count"], 0)
            self.assertTrue(ns["record_demonstration_step"](
                created["draft_id"], "set_subject", "Private launch"))

            metadata = ns["inspect_demonstration_drafts"]()
            self.assertEqual(metadata[0]["step_count"], 1)
            self.assertNotIn("Private launch", repr(metadata))
            revealed = ns["reveal_demonstration_draft"](
                created["draft_id"])
            self.assertEqual(revealed["steps"], ({
                "action": "set_subject", "text": "Private launch"},))
            self.assertTrue(ns["approve_demonstration_draft"](
                created["draft_id"]))
            self.assertEqual(
                ns["inspect_demonstration_drafts"]()[0]["state"],
                "approved")
            self.assertFalse(ns["cancel_demonstration_draft"](
                created["draft_id"]))
            self.assertTrue(ns["delete_approved_demonstration_draft"](
                created["draft_id"]))
            self.assertEqual(ns["inspect_demonstration_drafts"](), ())

    def test_runtime_cancel_rolls_back_and_non_mac_surface_is_closed(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "demonstrations.json"
            ns = self.namespace(path)
            created = ns["create_demonstration_draft"]("notes")
            self.assertTrue(ns["record_demonstration_step"](
                created["draft_id"], "set_note_body", "Remove me"))
            self.assertFalse(ns["delete_approved_demonstration_draft"](
                created["draft_id"]))
            self.assertTrue(ns["cancel_demonstration_draft"](
                created["draft_id"]))
            self.assertEqual(ns["inspect_demonstration_drafts"](), ())
            self.assertNotIn("Remove me", path.read_text(encoding="utf-8"))

            windows = self.namespace(path, is_macos=False)
            self.assertIsNone(windows["create_demonstration_draft"]("finder"))
            self.assertEqual(windows["inspect_demonstration_drafts"](), ())
            self.assertFalse(windows[
                "delete_approved_demonstration_draft"](demo_id(1)))

    def test_runtime_has_no_routine_or_execution_authority_path(self):
        routine = next(
            node for node in DICTATE_TREE.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "runtime_status_snapshot")
        routine_names = {
            node.id for node in ast.walk(routine) if isinstance(node, ast.Name)}
        self.assertFalse({
            "inspect_demonstration_drafts", "reveal_demonstration_draft",
        } & routine_names)

        feature_names = {
            "_demonstration_draft_store", "_demonstration_metadata",
            "inspect_demonstration_drafts", "create_demonstration_draft",
            "reveal_demonstration_draft", "record_demonstration_step",
            "approve_demonstration_draft", "cancel_demonstration_draft",
            "delete_approved_demonstration_draft",
        }
        feature_nodes = [
            node for node in DICTATE_TREE.body
            if isinstance(node, ast.FunctionDef) and node.name in feature_names
        ]
        storage_tree = ast.parse(
            (ROOT / "demonstration_drafts.py").read_text(encoding="utf-8"))
        identifiers = {
            node.id
            for tree in (*feature_nodes, storage_tree)
            for node in ast.walk(tree)
            if isinstance(node, ast.Name)
        }
        forbidden = {
            "subprocess", "socket", "requests", "AppKit", "Quartz",
            "AXUIElement", "pyautogui", "keyboard", "clipboard",
            "NSWorkspace",
        }
        self.assertFalse(forbidden & identifiers)


if __name__ == "__main__":
    unittest.main()
