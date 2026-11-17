# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///

import ast
import hashlib
import json
import re
import subprocess
import sys
import tempfile
import textwrap
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
LAST_MIT_COMMIT = "8f317df7ac5bb687ac8fbbfcd23abc1385be396d"
AGPL_SHA256 = "0d96a4ff68ad6d4b6f1f30f713b18d5184912ba8dd389f86aa7710db079abcb0"
WORKFLOWS = ROOT / ".github" / "workflows"
PULL_REQUEST_EVENTS = {"pull_request", "pull_request_target"}
HOSTED_RUNNER = re.compile(r"(ubuntu|macos|windows)-(latest|\d+(\.\d+)?)")


def workflow_triggers(source: str) -> set[str]:
    """The event names under a workflow's top-level ``on:`` key."""
    lines = source.splitlines()
    start = lines.index("on:")
    triggers = set()
    for line in lines[start + 1:]:
        if line and not line.startswith(" "):
            break
        match = re.match(r"^  ([a-z_]+):", line)
        if match:
            triggers.add(match.group(1))
    return triggers


def workflow_runners(source: str) -> list[str]:
    return re.findall(r"(?m)^\s+runs-on:\s*(.+?)\s*$", source)


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

    def test_no_pull_request_job_runs_on_a_self_hosted_runner(self):
        # A persistent runner that takes pull-request jobs runs a returning
        # contributor's code on the owner's machine, and a required check
        # pinned to one blocks every merge when that machine goes away.
        checked = 0
        for path in sorted(WORKFLOWS.glob("*.y*ml")):
            source = path.read_text(encoding="utf-8")
            if not workflow_triggers(source) & PULL_REQUEST_EVENTS:
                continue
            for runner in workflow_runners(source):
                checked += 1
                with self.subTest(workflow=path.name, runner=runner):
                    self.assertNotIn("self-hosted", runner)
                    self.assertRegex(runner, HOSTED_RUNNER)
        self.assertGreaterEqual(checked, 4)

    def test_cla_check_runs_the_base_copy_and_never_pull_request_code(self):
        source = self.read(".github/workflows/cla-check.yml")
        # The base branch's copy of this file decides; a pull request cannot
        # rewrite the check it is meant to pass.
        self.assertEqual(workflow_triggers(source), {"pull_request_target"})
        refs = re.findall(r"(?m)^\s+ref:\s*(.+?)\s*$", source)
        self.assertEqual(refs, ["${{ github.event.pull_request.base.sha }}"])
        for forbidden in ("pull_request.head", "github.head_ref",
                          "refs/pull/", "merge_commit_sha", "uv run",
                          "tests/", ": write"):
            with self.subTest(forbidden=forbidden):
                self.assertNotIn(forbidden, source)
        self.assertIn("persist-credentials: false", source)

    def test_pull_requests_run_the_full_release_gate_on_a_hosted_mac(self):
        source = self.read(".github/workflows/macos-gate.yml")
        self.assertIn("pull_request", workflow_triggers(source))
        self.assertTrue(all(runner.startswith("macos-")
                            for runner in workflow_runners(source)))
        # Execute the job's own script with every command recorded rather
        # than run: it must run exactly the AGENTS.md gate, in order.
        program = textwrap.dedent(
            source.split("python3 - <<'PY'\n", 1)[1].split("PY\n", 1)[0])
        agents = self.read("AGENTS.md")
        block = agents.split("```sh", 1)[1].split("```", 1)[0]
        expected = [line.strip() for line in block.splitlines()
                    if line.strip()]
        with tempfile.TemporaryDirectory() as directory:
            (Path(directory) / "AGENTS.md").write_text(
                agents, encoding="utf-8")
            harness = (
                "import json, subprocess, sys\n"
                "calls = []\n"
                "class Done:\n    returncode = 0\n"
                "subprocess.run = lambda command, shell: "
                "(calls.append(command), Done)[1]\n"
                "try:\n"
                f"    exec(compile({program!r}, 'gate', 'exec'))\n"
                "finally:\n"
                "    print(json.dumps(calls))\n")
            result = subprocess.run(
                [sys.executable, "-c", harness], cwd=directory,
                text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        ran = json.loads(result.stdout.strip().splitlines()[-1])
        self.assertEqual(ran, expected)
        self.assertGreater(len(ran), 50)

    def test_release_archives_and_the_index_carry_no_development_state(self):
        # A release bundle's working tree is `git archive` output. Without
        # export rules it held ~11.5 MB of unused render baselines, the wiki
        # tool's state, and a Cloudflare account cache. The bundle's shallow
        # .git still holds those blobs, so this keeps them out of the
        # installed folder, not out of the download.
        tracked = subprocess.run(
            ["git", "ls-files"], cwd=ROOT, text=True, capture_output=True,
            check=True).stdout.splitlines()
        self.assertEqual(
            [path for path in tracked if path.startswith(".wrangler/")], [])
        for path in (".DS_Store", "site/.DS_Store", ".wrangler/cache/x.json",
                     "site/.wrangler/state", "PACKAGE-CONTENTS.json",
                     "RELEASE-METADATA.json"):
            with self.subTest(ignored=path):
                ignored = subprocess.run(
                    ["git", "check-ignore", "-q", "--no-index", path],
                    cwd=ROOT)
                self.assertEqual(ignored.returncode, 0)
        archive = subprocess.run(
            ["git", "archive", "--worktree-attributes", "HEAD"],
            cwd=ROOT, capture_output=True, check=True).stdout
        # "-f -" is not optional: without it the tar that ships with Windows
        # reads a tape drive, not the pipe.
        listing = subprocess.run(
            ["tar", "-t", "-f", "-"], input=archive, capture_output=True,
            check=True).stdout.decode("utf-8").splitlines()
        self.assertIn("dictate.py", listing)
        for prefix in (".probe-", ".raw/", "wiki/.llm-wiki/", ".wrangler/",
                       ".claude/", ".impeccable/"):
            with self.subTest(prefix=prefix):
                self.assertEqual(
                    [entry for entry in listing if entry.startswith(prefix)],
                    [])

    # The Project Owner approved one exception to "never embed runtime
    # source" on 2026-11-16: the Mac app built for drag install. Both places
    # that state the rule have to state the exception in these words, so it
    # cannot quietly widen into "installers may embed the runtime".
    DRAG_INSTALL_RULE = (
        "An app built for drag install carries the exact published source "
        "archive of its own release, verified against its checksum before "
        "use, and installs from it; installers still execute the checkout as "
        "the single source of truth and never embed runtime source."
    )

    def test_the_drag_install_exception_is_stated_exactly_where_the_rule_is(self):
        for relative in ("AGENTS.md", "docs/installer-release-process.md"):
            with self.subTest(document=relative):
                text = " ".join(self.read(relative).split())
                self.assertIn(self.DRAG_INSTALL_RULE, text)
                # The old blanket wording is gone, so the two cannot be read
                # as contradicting each other.
                self.assertNotIn("must never embed a frozen copy", text)
        process = " ".join(
            self.read("docs/installer-release-process.md").split())
        self.assertIn("never runs anything from the archive itself", process)
        self.assertIn("runs that checkout's own `setup.sh`", process)

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

    # Every page that states the roster in English. Checking only the ones we
    # happened to remember is how the wiki ended up three revisions behind the
    # README while CI stayed green.
    FACE_ROSTER_PAGES = (
        "README.md",
        "docs/capabilities.md",
        "wiki/whisper-face.md",
        "wiki/whisper-faces.md",
        "wiki/marketing-site.md",
    )

    def test_documented_face_roster_matches_the_shipped_one(self):
        """Prose that counts the companions has to count the real ones.

        These pages name the roster in English, which quietly goes stale every
        time a face is added. Deriving the expectation from ``FACE_CHOICES``
        means the drift fails here instead of in front of a reader.
        """
        faces = self.face_choices()
        self.assertGreater(len(faces), 1, "expected a roster to check")
        counted = self.number_word(len(faces))

        for page in self.FACE_ROSTER_PAGES:
            found = self.ROSTER_COUNT.findall(self.read(page))
            with self.subTest(page=page):
                self.assertTrue(
                    found, f"{page} no longer states the roster size at all")
                for word in found:
                    self.assertEqual(
                        word.lower(), counted,
                        f"{page} claims {word} characters, but {counted} ship")

        readme = self.read("README.md")
        roster = self.read("wiki/whisper-faces.md")
        for face in faces:
            self.assertIn(
                face.capitalize(), readme,
                f"README does not name the shipped {face} face")
            self.assertIn(
                face.capitalize(), roster,
                f"wiki/whisper-faces.md does not name the shipped {face} face")

    # Comments that do arithmetic on the roster size. The prose pages above
    # are caught by ROSTER_COUNT; these count in shapes that regex will never
    # see -- "a sixteen-chip picker", "Sixteen across a 704pt card" -- and so
    # they sat a release stale after #139 while every documented page was
    # green. Historical asides are deliberately absent: "the ten-face row" in
    # the picker is where the 24pt gap cap comes from and must stay ten.
    FACE_ROSTER_SOURCES = (
        ("whisper_face_theme.py", r"\b([a-z]+)-chip picker\b"),
        ("whisper_face_gui.py",
         r"One row until the chips would crowd\. ([A-Za-z]+)\b"),
        ("site/src/data/faces.ts", r"^// The ([A-Za-z]+) Whisper Face faces\b"),
    )

    def test_source_comments_that_count_the_roster_stay_current(self):
        """Comments that state the roster size have to state the real one.

        Prose is not the only place the count is written down: the chip
        palette explains itself in terms of picker width, and the picker
        wraps against a worked example. Deriving both from ``FACE_CHOICES``
        means the next companion fails here rather than leaving a comment
        that quietly argues from the wrong number.
        """
        counted = self.number_word(len(self.face_choices()))
        for path, pattern in self.FACE_ROSTER_SOURCES:
            found = re.findall(pattern, self.read(path), re.MULTILINE)
            with self.subTest(path=path):
                self.assertTrue(
                    found,
                    f"{path} no longer counts the roster where it used to; "
                    "update FACE_ROSTER_SOURCES with its new wording")
                for word in found:
                    self.assertEqual(
                        word.lower(), counted,
                        f"{path} reasons about {word} faces, "
                        f"but {counted} ship")

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

    NUMBER_WORDS = (
        "zero", "one", "two", "three", "four", "five", "six", "seven",
        "eight", "nine", "ten", "eleven", "twelve", "thirteen", "fourteen",
        "fifteen", "sixteen", "seventeen", "eighteen", "nineteen", "twenty",
    )

    @classmethod
    def number_word(cls, value: int) -> str:
        if value >= len(cls.NUMBER_WORDS):
            raise AssertionError(
                f"extend NUMBER_WORDS past {len(cls.NUMBER_WORDS) - 1} "
                f"for {value} faces")
        return cls.NUMBER_WORDS[value]

    # A spelled-out number in front of "characters" is a claim about the
    # roster however it is dressed up -- "ten characters", "the ten chibi-clay
    # companion characters". Catch the count wherever it sits so a page cannot
    # keep a stale one simply by rewording around a fixed phrase. "one" is left
    # out: it reads as ordinary prose about a single character, not a total.
    ROSTER_COUNT = re.compile(
        r"\b(" + "|".join(w for w in NUMBER_WORDS if w != "one") + r")\b"
        r"(?:\s+[\w-]+){0,3}?\s+characters\b",
        re.IGNORECASE,
    )


if __name__ == "__main__":
    unittest.main()
