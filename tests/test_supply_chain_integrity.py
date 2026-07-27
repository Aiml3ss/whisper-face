# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Fail-closed checks on what this build actually depends on.

The project already pins Python packages in ``dictate.py.lock``, model weights
by immutable revision, and release actions by commit. Pinning is only half of
the control: nothing previously compared those pins against each other, so the
same model could be recorded one way in the runtime, another way in the model
wallet, and a third way in the notices without anything failing.

These tests close that gap on every pull request:

* every direct dependency the lock actually installs is documented, at the
  version the lock installs;
* every pinned model revision is identical in the runtime, the model wallet,
  the benchmark scorecard, and the third-party notices, and is a content
  address rather than a moving tag;
* every GitHub action any workflow runs is pinned to a full commit and
  recorded in the notices, and every workflow states its token permissions.

Everything here reads committed files. There is no network access, so a
mirror, registry, or model host cannot influence the result.
"""

import ast
import json
import re
import unittest
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
WORKFLOWS = ROOT / ".github" / "workflows"

IMMUTABLE_REVISION = re.compile(r"^[0-9a-f]{40}$")
IMMUTABLE_DIGEST = re.compile(r"^sha256:[0-9a-f]{64}$")
VERSION_TOKEN = re.compile(r"\b\d+(?:\.\d+)*\b")
USES_LINE = re.compile(r"^\s*(?:-\s*)?uses:\s*(\S+)\s*(?:#\s*(\S+))?\s*$", re.M)
PINNED_USES = re.compile(r"^([\w.-]+/[\w.-]+)@([0-9a-f]{40})$")

# Each row of the notices' direct-dependency table, and the lock package names
# it speaks for. "PyObjC frameworks" is one row for four packages that are
# always released together; every other row is one package.
NOTICE_PACKAGES = {
    "mlx-whisper": ("mlx-whisper",),
    "faster-whisper": ("faster-whisper",),
    "sounddevice": ("sounddevice",),
    "pynput": ("pynput",),
    "PyObjC frameworks": (
        "pyobjc-core",
        "pyobjc-framework-applicationservices",
        "pyobjc-framework-cocoa",
        "pyobjc-framework-quartz",
    ),
    "pyperclip": ("pyperclip",),
    "pywin32": ("pywin32",),
    "pystray": ("pystray",),
    "Pillow": ("pillow",),
    "NumPy": ("numpy",),
    "Requests": ("requests",),
    "tqdm": ("tqdm",),
}

# faster-whisper resolves these short model names to Hugging Face
# repositories. The runtime only ever writes the short name, so the notices are
# the one place the binding is recorded, and the one place it can rot.
WINDOWS_ASR_REPOSITORIES = {
    "tiny": "Systran/faster-whisper-tiny",
    "turbo": "mobiuslabsgmbh/faster-whisper-large-v3-turbo",
}

# The FluidAudio Swift package is pinned like a model but is not one, so it is
# absent from every runtime model constant. Naming it here keeps the
# "documented revisions minus runtime revisions" comparison exact.
FLUIDAUDIO_PACKAGE_REVISION = "19600a485baa4998812e4654b70d2bab8f2c9949"


def _read(relative: str) -> str:
    return (ROOT / relative).read_text(encoding="utf-8")


def _table_rows(text: str, heading: str) -> list[list[str]]:
    """Cells of every body row in the markdown table under ``heading``."""
    start = text.index(f"## {heading}")
    end = text.find("\n## ", start + 1)
    section = text[start:end if end != -1 else len(text)]
    rows = []
    for line in section.splitlines():
        line = line.strip()
        if not line.startswith("|") or set(line) <= set("|-: "):
            continue
        cells = [cell.strip() for cell in line.strip("|").split("|")]
        rows.append(cells)
    return rows[1:] if rows else []


def _locked_packages() -> dict[str, set[str]]:
    lock = _read("dictate.py.lock")
    packages: dict[str, set[str]] = {}
    for block in lock.split("[[package]]")[1:]:
        name = re.search(r'^name = "([^"]+)"', block, re.M)
        version = re.search(r'^version = "([^"]+)"', block, re.M)
        if name and version:
            packages.setdefault(name.group(1), set()).add(version.group(1))
    return packages


def _locked_direct_names() -> set[str]:
    lock = _read("dictate.py.lock")
    manifest = lock.split("[manifest]")[1].split("[[package]]")[0]
    return set(re.findall(r'\{ name = "([^"]+)"', manifest))


def _declared_dependency_names() -> set[str]:
    """Names from dictate.py's PEP 723 block, normalised the way uv does."""
    source = _read("dictate.py")
    start = source.index("# dependencies = [")
    block = source[start:source.index("# ]", start)]
    names = set()
    for requirement in re.findall(r'"([^"]+)"', block):
        name = requirement.split(";")[0].strip()
        name = re.split(r"[<>=!~\[ ]", name)[0]
        names.add(name.lower().replace("_", "-").replace(".", "-"))
    return names


def _runtime_constants() -> dict[str, object]:
    tree = ast.parse(_read("dictate.py"))
    wanted = {
        "ASR_MODEL_REVISIONS", "PARAKEET_MODEL_REPO", "PARAKEET_MODEL_REVISION",
        "OLLAMA_MODEL", "OLLAMA_MODEL_MANIFEST_SHA256",
    }
    constants: dict[str, object] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign):
            continue
        for target in node.targets:
            if isinstance(target, ast.Name) and target.id in wanted:
                try:
                    constants[target.id] = ast.literal_eval(node.value)
                except ValueError:
                    pass
    return constants


def _runtime_pins() -> dict[str, str]:
    """Every model this build may load, as repository -> immutable revision."""
    constants = _runtime_constants()
    pins = {}
    for name, revision in constants["ASR_MODEL_REVISIONS"].items():
        pins[WINDOWS_ASR_REPOSITORIES.get(name, name)] = revision
    pins[constants["PARAKEET_MODEL_REPO"]] = constants["PARAKEET_MODEL_REVISION"]
    pins[constants["OLLAMA_MODEL"]] = (
        f"sha256:{constants['OLLAMA_MODEL_MANIFEST_SHA256']}")
    return pins


def _wallet_pins() -> dict[str, str]:
    """Every ModelIdentity the wallet declares, however it is spelled.

    Positional and keyword forms are both valid dataclass calls, so both are
    parsed; a call this extractor cannot read is a hard failure rather than a
    silent omission, because a skipped identity is exactly how an absent or
    drifted pin would evade the audit while the wallet looks populated.
    """
    pins = {}
    for node in ast.walk(ast.parse(_read("model_wallet.py"))):
        if not isinstance(node, ast.Call):
            continue
        name = node.func.id if isinstance(node.func, ast.Name) else None
        if name != "ModelIdentity":
            continue
        values = {}
        try:
            for field, argument in zip(("model_id", "revision"), node.args):
                values[field] = ast.literal_eval(argument)
            for keyword in node.keywords:
                if keyword.arg is None:
                    raise ValueError("**kwargs is not auditable")
                values[keyword.arg] = ast.literal_eval(keyword.value)
        except ValueError as error:
            raise AssertionError(
                f"model_wallet.py line {node.lineno}: ModelIdentity call "
                f"cannot be audited statically ({error}); write literal "
                "model_id and revision") from error
        if set(values) < {"model_id", "revision"}:
            raise AssertionError(
                f"model_wallet.py line {node.lineno}: ModelIdentity call "
                f"names only {sorted(values)}; the audit needs both "
                "model_id and revision as literals")
        pins[values["model_id"]] = values["revision"]
    return pins


class LockedDependencyProvenanceTests(unittest.TestCase):
    """The notices have to describe the dependency graph that ships."""

    def test_every_locked_direct_dependency_is_documented(self):
        documented = set()
        for packages in NOTICE_PACKAGES.values():
            documented.update(packages)
        missing = _locked_direct_names() - documented
        self.assertEqual(
            missing, set(),
            f"the lock installs {sorted(missing)} directly, and "
            "THIRD_PARTY_NOTICES.md does not record its license or upstream. "
            "Every dependency's provenance must be written down before it "
            "ships.")

    def test_documented_versions_are_the_versions_the_lock_installs(self):
        locked = _locked_packages()
        rows = {cells[0]: cells for cells
                in _table_rows(_read("THIRD_PARTY_NOTICES.md"),
                               "Direct Python runtime dependencies")}
        self.assertEqual(set(rows), set(NOTICE_PACKAGES))
        for component, packages in NOTICE_PACKAGES.items():
            with self.subTest(component=component):
                claimed = set(VERSION_TOKEN.findall(rows[component][1]))
                installed = set()
                for package in packages:
                    self.assertIn(
                        package, locked,
                        f"{package} is documented but absent from the lock")
                    installed |= locked[package]
                self.assertEqual(
                    claimed, installed,
                    f"THIRD_PARTY_NOTICES.md claims {component} is "
                    f"{sorted(claimed)}; the lock installs {sorted(installed)}")

    def test_the_declared_block_and_the_lock_manifest_agree(self):
        declared = _declared_dependency_names()
        locked = {name.lower() for name in _locked_direct_names()}
        self.assertEqual(
            declared, locked,
            "dictate.py's PEP 723 dependency block and dictate.py.lock's "
            "manifest name different packages; regenerate the lock")

    def test_every_documented_row_names_a_license_and_an_upstream(self):
        for cells in _table_rows(_read("THIRD_PARTY_NOTICES.md"),
                                 "Direct Python runtime dependencies"):
            with self.subTest(component=cells[0]):
                self.assertTrue(cells[2], "license cell is empty")
                self.assertIn("https://", cells[3])


class ModelRevisionPinningTests(unittest.TestCase):
    """One model, one revision, recorded identically everywhere."""

    @classmethod
    def setUpClass(cls):
        cls.runtime = _runtime_pins()
        cls.wallet = _wallet_pins()
        cls.notices = _read("THIRD_PARTY_NOTICES.md")

    def test_every_pin_is_an_immutable_content_address(self):
        self.assertTrue(self.runtime)
        for repository, revision in self.runtime.items():
            with self.subTest(repository=repository):
                self.assertTrue(
                    IMMUTABLE_REVISION.match(revision)
                    or IMMUTABLE_DIGEST.match(revision),
                    f"{repository} is pinned to {revision!r}, which is a "
                    "moving reference rather than a content address")

    def test_the_model_wallet_agrees_with_the_runtime(self):
        self.assertTrue(self.wallet)
        for repository, revision in self.wallet.items():
            with self.subTest(repository=repository):
                self.assertIn(
                    repository, self.runtime,
                    "the model wallet offers a provider the runtime does not "
                    "pin")
                self.assertEqual(
                    revision, self.runtime[repository],
                    f"model_wallet.py and dictate.py disagree about "
                    f"{repository}")

    def test_the_benchmark_scorecard_measures_the_shipped_revisions(self):
        scorecard = json.loads(_read("benchmarks/model_scorecard.json"))
        candidates = scorecard["candidates"]
        self.assertTrue(candidates)
        for candidate in candidates:
            repository = candidate["model_id"]
            revision = candidate["revision"]
            with self.subTest(repository=repository):
                self.assertEqual(
                    revision, self.runtime.get(repository),
                    "the scorecard ranks a revision this build does not load")
                self.assertEqual(
                    candidate["revision_api_url"],
                    "https://huggingface.co/api/models/"
                    f"{repository}/revision/{revision}",
                    "the weekly audit would check a different revision than "
                    "the one that ships")

    def test_every_shipped_revision_is_documented_beside_its_repository(self):
        for repository, revision in self.runtime.items():
            with self.subTest(repository=repository):
                rows = [line for line in self.notices.splitlines()
                        if repository in line]
                self.assertTrue(
                    rows,
                    f"{repository} is loaded at runtime and appears nowhere "
                    "in THIRD_PARTY_NOTICES.md")
                self.assertTrue(
                    any(revision.removeprefix("sha256:") in row
                        for row in rows),
                    f"THIRD_PARTY_NOTICES.md names {repository} but not the "
                    f"revision the runtime pins ({revision})")

    def test_the_notices_document_no_revision_the_build_does_not_use(self):
        model_tables = "\n".join(
            "|".join(cells)
            for heading in ("Native Mac recognition helper",
                            "Speech and cleanup models")
            for cells in _table_rows(self.notices, heading))
        documented = set(re.findall(r"`([0-9a-f]{40})`", model_tables))
        documented |= set(
            re.findall(r"`sha256:([0-9a-f]{64})`", model_tables))
        shipped = {revision.removeprefix("sha256:")
                   for revision in self.runtime.values()}
        self.assertEqual(
            documented - shipped, {FLUIDAUDIO_PACKAGE_REVISION},
            "THIRD_PARTY_NOTICES.md pins a revision that nothing in this "
            "build loads; a stale pin is how a re-audit gets skipped")

    def test_the_selective_relisten_verifier_uses_a_pinned_model(self):
        # relisten_activation.py and whisper_verifier_adapter.py name the
        # verifier repository *and revision* independently of dictate.py.
        # Checking only the repository name would let either module's
        # revision drift while this assertion stayed green, so both halves
        # of each pin are compared against what the runtime loads.
        for module in ("relisten_activation.py", "whisper_verifier_adapter.py"):
            with self.subTest(module=module):
                source = _read(module)
                repositories = re.findall(r'"(mlx-community/[\w.-]+)"', source)
                self.assertTrue(repositories)
                for repository in repositories:
                    self.assertIn(
                        repository, self.runtime,
                        f"{module} names a model the runtime does not pin")
                revisions = re.findall(
                    r'REVISION\s*=\s*"([0-9a-f]{40})"', source)
                self.assertTrue(
                    revisions,
                    f"{module} no longer pins a literal model revision")
                for revision in revisions:
                    self.assertIn(
                        revision, set(self.runtime.values()),
                        f"{module} pins revision {revision}, which the "
                        "runtime does not load; the verifier and the "
                        "recognizer must ship the same model")


class WorkflowSupplyChainTests(unittest.TestCase):
    """CI must not run code chosen by a moving tag."""

    @classmethod
    def setUpClass(cls):
        # Both suffixes: GitHub executes *.yml and *.yaml alike, so a
        # workflow added under the other extension must not slip past the
        # pinning and permissions assertions.
        cls.workflows = sorted(
            list(WORKFLOWS.glob("*.yml")) + list(WORKFLOWS.glob("*.yaml")))
        cls.sources = {path.name: path.read_text(encoding="utf-8")
                       for path in cls.workflows}

    def test_there_are_workflows_to_check(self):
        self.assertGreaterEqual(len(self.workflows), 5)

    def test_every_action_is_pinned_to_a_full_commit(self):
        for name, source in self.sources.items():
            for reference, comment in USES_LINE.findall(source):
                with self.subTest(workflow=name, uses=reference):
                    self.assertRegex(
                        reference, PINNED_USES,
                        f"{name} runs {reference}, which a tag owner can "
                        "repoint at different code between runs")
                    self.assertTrue(
                        comment,
                        f"{name} pins {reference} without recording which "
                        "release the commit is")

    def test_every_pinned_action_is_recorded_in_the_notices(self):
        # Accumulate every occurrence rather than keeping the last one per
        # action: the same action pinned differently in two workflows is
        # exactly the drift this test exists to catch.
        used = {}
        for source in self.sources.values():
            for reference, comment in USES_LINE.findall(source):
                match = PINNED_USES.match(reference)
                if match:
                    used.setdefault(match.group(1), set()).add(
                        (match.group(2), comment))
        documented = {}
        for cells in _table_rows(_read("THIRD_PARTY_NOTICES.md"),
                                 "Release automation actions"):
            action = re.match(r"`([\w.-]+/[\w.-]+)`\s*(\S+)", cells[0])
            revision = re.match(r"`([0-9a-f]{40})`", cells[1])
            self.assertTrue(action and revision, f"unreadable row: {cells}")
            documented[action.group(1)] = (revision.group(1), action.group(2))
        self.assertEqual(
            set(used), set(documented),
            "the actions CI runs and the actions the notices record differ: "
            f"undocumented {sorted(set(used) - set(documented))}, "
            f"unused {sorted(set(documented) - set(used))}")
        for action, occurrences in used.items():
            with self.subTest(action=action):
                self.assertEqual(
                    occurrences, {documented[action]},
                    f"{action} runs at a revision or release the notices do "
                    f"not record (or at two different ones): {occurrences}")

    def test_every_workflow_states_its_token_permissions(self):
        for name, source in self.sources.items():
            with self.subTest(workflow=name):
                self.assertRegex(
                    source, r"(?m)^permissions:\s*$",
                    f"{name} inherits whatever the repository default token "
                    "grants; state the permissions it needs")

    def test_the_pull_request_gate_verifies_the_lock(self):
        # The lock is the only thing standing between a pull request and an
        # unreviewed package. Verifying it at release time is too late.
        smoke = self.sources["windows-smoke.yml"]
        self.assertIn("pull_request", smoke)
        self.assertIn("uv lock --check --script dictate.py", smoke)
        self.assertIn("uv sync --locked --script dictate.py", smoke)
        for gate in (
            "uv run tests/test_network_egress.py",
            "uv run tests/test_supply_chain_integrity.py",
        ):
            with self.subTest(gate=gate):
                self.assertIn(gate, smoke)


class ReleaseProvenanceTests(unittest.TestCase):
    """A downloader must be able to prove which build produced an artifact."""

    @classmethod
    def setUpClass(cls):
        cls.workflow = _read(".github/workflows/macos-release.yml")

    def test_published_artifacts_carry_build_provenance(self):
        self.assertIn("actions/attest-build-provenance@", self.workflow)
        self.assertIn("subject-checksums: dist/SHA256SUMS", self.workflow)

    def test_provenance_signing_needs_no_long_lived_project_key(self):
        # The attestation is signed with a workflow-scoped OIDC identity, so
        # the permission block is the whole key-management story.
        publish = self.workflow[self.workflow.index("  publish-release:"):]
        for permission in ("id-token: write", "attestations: write"):
            with self.subTest(permission=permission):
                self.assertIn(permission, publish)

    def test_apple_credentials_stay_out_of_the_attesting_job(self):
        publish = self.workflow[self.workflow.index("  publish-release:"):]
        self.assertNotIn("APPLE_", publish)

    def test_the_release_notes_tell_a_downloader_how_to_verify(self):
        self.assertIn("gh attestation verify", self.workflow)

    def test_provenance_is_documented_where_the_guarantees_are_listed(self):
        provenance = _read("docs/security/release-provenance.md")
        for required in (
            "gh attestation verify",
            "SHA256SUMS",
            "does not",
        ):
            with self.subTest(required=required):
                self.assertIn(required, provenance)
        threat_model = _read("docs/security/threat-model.md")
        self.assertIn("release-provenance.md", threat_model)


if __name__ == "__main__":
    unittest.main()
