# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import ast
import hashlib
import json
import subprocess
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAST_MIT_COMMIT = "8f317df7ac5bb687ac8fbbfcd23abc1385be396d"
AGPL_SHA256 = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"


class RepositoryGovernanceTests(unittest.TestCase):
    def read(self, relative: str) -> str:
        return (ROOT / relative).read_text(encoding="utf-8")

    def test_current_license_is_canonical_agpl_only(self):
        license_text = self.read("LICENSE")
        policy = self.read("LICENSE_POLICY.md")
        notice = self.read("NOTICE")
        self.assertIn("GNU AFFERO GENERAL PUBLIC LICENSE", license_text)
        self.assertIn("Version 3, 19 November 2007", license_text)
        self.assertIn("AGPL-3.0-only", policy)
        self.assertNotIn("AGPL-3.0-or-later", policy)
        self.assertIn("Copyright (C) 2026 Andrew Bergstrom", notice)
        self.assertEqual(
            hashlib.sha256((ROOT / "LICENSE").read_bytes()).hexdigest(),
            AGPL_SHA256,
        )

    def test_historical_mit_boundary_is_preserved(self):
        policy = self.read("LICENSE_POLICY.md")
        historical = self.read("LICENSES/MIT.txt")
        self.assertIn(LAST_MIT_COMMIT, policy)
        self.assertIn("remain valid", policy)
        self.assertIn("MIT License", historical)
        self.assertIn("Copyright (c) 2026 Andrew Bergstrom", historical)
        scope = self.read("LICENSES/README.md")
        self.assertIn("does **not** offer the current source", scope)
        historical_from_git = subprocess.run(
            ["git", "show", f"{LAST_MIT_COMMIT}:LICENSE"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout
        self.assertEqual(historical_from_git, historical)
        subprocess.run(
            ["git", "merge-base", "--is-ancestor", LAST_MIT_COMMIT, "HEAD"],
            cwd=ROOT,
            check=True,
        )
        tag_target = subprocess.run(
            ["git", "rev-list", "-n", "1", "mit-last"],
            cwd=ROOT,
            text=True,
            capture_output=True,
            check=True,
        ).stdout.strip()
        self.assertEqual(tag_target, LAST_MIT_COMMIT)

    def test_commercial_path_is_separate_and_not_self_executing(self):
        policy = self.read("LICENSE_POLICY.md")
        commercial = self.read("COMMERCIAL_LICENSE.md")
        self.assertIn("separate written commercial license", policy)
        self.assertIn("No commercial rights are granted by this document", commercial)
        self.assertIn("third-party", commercial.lower())

    def test_contributions_require_recorded_cla_acceptance(self):
        contributing = self.read("CONTRIBUTING.md")
        cla = self.read("CLA.md")
        template = self.read(".github/pull_request_template.md")
        workflow = self.read(".github/workflows/cla-check.yml")
        codeowners = self.read(".github/CODEOWNERS")
        ledger = json.loads(self.read(".github/cla-signatures-v1.json"))
        process = self.read("docs/licensing-release-process.md")
        acceptance = (
            "I have read and agree to the Whisper Face Contributor License "
            "Agreement version 1.0."
        )
        normalized = " ".join(contributing.replace("> ", "").split())
        self.assertIn(acceptance, normalized)
        self.assertIn("You retain ownership", cla)
        self.assertIn("proprietary or commercial licenses", cla)
        self.assertIn("Grant of Copyright and Patent Rights", template)
        self.assertIn("CLA.md", template)
        self.assertIn("pull_request.base.sha", workflow)
        self.assertIn("project_owner", workflow)
        self.assertIn("/CLA.md @Aiml3ss", codeowners)
        self.assertIn("/.github/workflows/ @Aiml3ss", codeowners)
        self.assertEqual(ledger["project_owner"], "Aiml3ss")
        self.assertEqual(ledger["project_owner_id"], 1891520)
        self.assertEqual(ledger["cla_version"], "1.0")
        self.assertEqual(
            ledger["cla_sha256"],
            hashlib.sha256((ROOT / "CLA.md").read_bytes()).hexdigest(),
        )
        self.assertIn("CONTRIBUTOR_ID", workflow)
        self.assertIn("github_id", workflow)
        self.assertIn("must not merge", process)

    def test_readme_explains_license_and_historical_boundary(self):
        readme = self.read("README.md")
        self.assertIn("AGPL-3.0-only", readme)
        self.assertIn("commercial terms", readme.lower())
        self.assertIn("Earlier published commits remain MIT-licensed", readme)

    def test_free_core_support_and_concierge_pilot_are_honest(self):
        readme = self.read("README.md")
        support = " ".join(self.read("SUPPORT.md").lower().split())
        interest_form = self.read(
            ".github/ISSUE_TEMPLATE/supporter-concierge-interest.yml"
        ).lower()

        self.assertIn("[support and setup pilot](support.md)", readme.lower())
        for promise in (
            "no account or word cap",
            "never paywalled",
            "no payment account is connected",
            "stated interest only",
            "not revenue, conversion, or willingness-to-pay evidence",
            "do not include private data in the public issue",
        ):
            self.assertIn(promise, support)
        self.assertIn("nonbinding", interest_form)
        self.assertIn("do not include", interest_form)
        for forbidden_field in (
            "id: email", "id: phone", "id: transcript", "id: audio",
            "id: logs", "id: credentials", "id: payment",
        ):
            self.assertNotIn(forbidden_field, interest_form)

    def test_architecture_docs_preserve_in_process_protocol_boundary(self):
        readme = self.read("README.md")
        architecture = self.read("docs/architecture-and-interop.md")
        contributing = self.read("CONTRIBUTING.md")

        self.assertIn(
            "docs/architecture-and-interop.md", readme)
        self.assertIn("in-process conformance contract", architecture)
        self.assertIn(
            "does **not** currently ship a public cross-process SDK",
            architecture)
        self.assertIn("public ABI", architecture)
        self.assertIn("voice_input_protocol_wire.py", architecture)
        self.assertIn("bounded POSIX transport and", architecture)
        self.assertIn("one-shot network-denial worker", architecture)
        self.assertIn("voice_input_protocol_transport.py", architecture)
        self.assertIn("macos_networkless_worker.py", architecture)
        self.assertIn("uv run tests/test_voice_input_protocol.py", contributing)
        self.assertIn(
            "Both installers must execute the current checkout", contributing)

    def test_documented_face_roster_matches_the_shipped_one(self):
        """Prose that counts the companions has to count the real ones.

        Both the README and the capability inventory name the roster in
        English, which quietly goes stale every time a face is added. Deriving
        the expectation from ``FACE_CHOICES`` means the drift fails here
        instead of in front of a reader.
        """
        faces = self.face_choices()
        readme = self.read("README.md")
        capabilities = self.read("docs/capabilities.md")

        self.assertGreater(len(faces), 1, "expected a roster to check")
        counted = self.number_word(len(faces))

        for face in faces:
            self.assertIn(
                face.capitalize(), readme,
                f"README does not name the shipped {face} face")

        self.assertIn(f"{counted} characters", readme)
        self.assertIn(f"{counted} animated companion characters", capabilities)

    def face_choices(self) -> tuple[str, ...]:
        """Read ``FACE_CHOICES`` out of ``dictate.py`` without importing it."""
        module = ast.parse(self.read("dictate.py"))
        for node in module.body:
            if not isinstance(node, ast.Assign):
                continue
            names = [t.id for t in node.targets if isinstance(t, ast.Name)]
            if "FACE_CHOICES" in names:
                return tuple(ast.literal_eval(node.value))
        self.fail("FACE_CHOICES is no longer a module-level assignment")

    @staticmethod
    def number_word(value: int) -> str:
        words = (
            "zero", "one", "two", "three", "four", "five", "six", "seven",
            "eight", "nine", "ten", "eleven", "twelve", "thirteen",
            "fourteen", "fifteen", "sixteen", "seventeen", "eighteen",
            "nineteen", "twenty",
        )
        if value >= len(words):
            raise AssertionError(
                f"extend number_word() past {len(words) - 1} for {value} faces")
        return words[value]


if __name__ == "__main__":
    unittest.main()
