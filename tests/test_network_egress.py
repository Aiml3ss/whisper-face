# /// script
# requires-python = ">=3.10"
# dependencies = []
# ///
"""Privacy regression tests for outbound network traffic.

The product's central promise is that speech never leaves the machine. These
tests defend that promise from two directions at once, because either one on
its own has a hole:

*Structurally*, no first-party module reachable from ``dictate.py`` may import
a network client. ``dictate.py`` itself is the single audited exception, and
every call site inside it that can open a socket is enumerated here by name.
A static check cannot see through ``getattr`` or a late import, so it is not
enough on its own.

*Dynamically*, a synthetic utterance is driven through the real compile path
with ``socket``, ``urllib.request``, and ``http.client`` replaced by a
tripwire. Any connection attempt is recorded and blocked. A dynamic check only
covers the code the test happens to execute, so it is not enough on its own
either. Together they leave very little room: a new outbound call has to both
avoid importing a client and avoid the instrumented entry points.

The tripwire is itself tested. ``EgressTripwireTests`` proves it catches raw
sockets, ``create_connection``, ``urlopen``, ``http.client``, and name
resolution, so a green run here is evidence rather than an unarmed alarm.

Nothing in this file touches the real network, real audio, or a real model.
"""

import ast
import http.client
import socket
import sys
import unittest
import urllib.parse
import urllib.request
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(ROOT))

from parrot_core import compile_cleanup  # noqa: E402
from voice_compiler import (  # noqa: E402
    ContextObservation,
    ContextRouter,
    PersonalPrior,
    RecognitionHypothesis,
    VoiceCompiler,
    VoiceIR,
    WordEvidence,
    analyze_prosody,
    build_consequence_plan,
    context_firewall_receipt,
    protected_anchors,
)

# Ollama and the app's own health endpoint are the only sockets the product is
# allowed to touch while dictating. Model downloads reach Hugging Face, but
# only from the installer and the preload path, never from this pipeline.
ALLOWED_ENDPOINTS = frozenset({
    ("127.0.0.1", 11434),
    ("localhost", 11434),
    ("::1", 11434),
    ("127.0.0.1", 8787),
    ("localhost", 8787),
    ("::1", 8787),
})

# Importing any of these gives a module the ability to originate or accept
# network traffic. ``urllib.parse`` is deliberately absent: it only splits
# strings, and the runtime uses it to read inbound request paths.
NETWORK_CLIENT_MODULES = frozenset({
    "socket", "socketserver", "ssl", "requests", "httpx", "aiohttp",
    "urllib3", "websocket", "websockets", "ftplib", "smtplib", "poplib",
    "imaplib", "nntplib", "telnetlib", "http.client", "http.server",
    "urllib.request", "urllib.error", "xmlrpc.client", "xmlrpc.server",
})

# The one module that is allowed to hold a network client, and exactly which
# clients it may hold. Anything else is a new outbound data flow, which
# security invariant 7 says requires consent and a threat-model update.
AUDITED_NETWORK_MODULE = "dictate"
AUDITED_NETWORK_IMPORTS = frozenset({"socket", "requests", "http.server"})

# Every function in dictate.py that can open, bind, or read a socket, and what
# each one is for. The set is asserted exactly: a sixth entry means someone
# added a network call that nobody has reviewed.
AUDITED_CALL_SITES = {
    # The only outbound HTTP client in the product. Target is OLLAMA_URL. The
    # call sits in a closure so the schema-rejection retry can reuse it (#126).
    "ollama_chat.post": frozenset({"requests.post"}),
    # Route discovery for the printed LAN address. UDP connect assigns a
    # source address; it transmits nothing. Server-only mode reaches it.
    "lan_ip": frozenset({"socket.socket", "s.connect"}),
    # The inbound compatibility endpoint. Loopback unless --server-only.
    "phone_server": frozenset(
        {"http.server.ThreadingHTTPServer", "srv.serve_forever"}),
    # A private AF_UNIX activation socket. No IP family is involved.
    "start_gui_activation_server": frozenset(
        {"socket.socket", "listener.bind", "listener.listen"}),
    "start_gui_activation_server.serve": frozenset(
        {"listener.accept", "connection.recv"}),
}

_CLIENT_CALL_ROOTS = frozenset({
    "requests", "httpx", "aiohttp", "urllib3", "socket"})
_CLIENT_CALL_PREFIXES = frozenset({
    "urllib.request", "urllib.error", "http.client", "http.server"})
_SOCKET_VERBS = frozenset({
    "connect", "connect_ex", "bind", "listen", "accept", "sendto", "sendall",
    "urlopen", "create_connection", "getaddrinfo", "gethostbyname",
    "serve_forever", "recv", "recvfrom",
})
_SERVER_TYPES = frozenset({"ThreadingHTTPServer", "HTTPServer"})


def _module_path(name: str) -> Path | None:
    for base in (ROOT, ROOT / "scripts"):
        candidate = base / f"{name}.py"
        if candidate.is_file():
            return candidate
    return None


def _imported_modules(path: Path) -> set[str]:
    """Every module name imported anywhere in a file, nested imports included."""
    names: set[str] = set()
    for node in ast.walk(ast.parse(path.read_text(encoding="utf-8"))):
        if isinstance(node, ast.Import):
            names.update(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            names.add(node.module)
    return names


def _network_imports(path: Path) -> set[str]:
    found = set()
    for name in _imported_modules(path):
        parts = name.split(".")
        for depth in range(1, len(parts) + 1):
            prefix = ".".join(parts[:depth])
            if prefix in NETWORK_CLIENT_MODULES:
                found.add(prefix)
    return found


def _first_party_closure(entry: str) -> dict[str, set[str]]:
    """Map every first-party module reachable from ``entry`` to its network imports."""
    first_party = {path.stem for path in ROOT.glob("*.py")}
    first_party |= {path.stem for path in (ROOT / "scripts").glob("*.py")}
    reached: dict[str, set[str]] = {}
    pending = [entry]
    while pending:
        name = pending.pop()
        if name in reached:
            continue
        path = _module_path(name)
        if path is None:
            continue
        reached[name] = _network_imports(path)
        for imported in _imported_modules(path):
            root = imported.split(".")[0]
            if root in first_party and root not in reached:
                pending.append(root)
    return reached


def _dotted(node: ast.AST) -> str | None:
    parts: list[str] = []
    while isinstance(node, ast.Attribute):
        parts.append(node.attr)
        node = node.value
    if isinstance(node, ast.Name):
        parts.append(node.id)
        return ".".join(reversed(parts))
    return None


def _import_aliases(tree: ast.AST) -> dict[str, str]:
    """Map every name an import binds to the dotted path it stands for.

    ``import requests as client`` binds ``client`` to ``requests``;
    ``from requests import post as send`` binds ``send`` to ``requests.post``.
    Without this table, an ordinary alias would walk a call straight past the
    audit: the alias's written name matches no client root, and the audit
    would conclude the module makes no outbound calls at all.
    """
    aliases: dict[str, str] = {}
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            for alias in node.names:
                bound = alias.asname or alias.name.split(".")[0]
                target = alias.name if alias.asname else alias.name.split(".")[0]
                aliases[bound] = target
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            for alias in node.names:
                if alias.name == "*":
                    continue
                bound = alias.asname or alias.name
                aliases[bound] = f"{node.module}.{alias.name}"
    return aliases


def _resolve(dotted: str, aliases: dict[str, str]) -> str:
    root, _, rest = dotted.partition(".")
    canonical = aliases.get(root)
    if canonical is None:
        return dotted
    return f"{canonical}.{rest}" if rest else canonical


def _call_sites(path: Path) -> dict[str, set[str]]:
    """Map each enclosing function name to the socket-capable calls it makes.

    Nested helpers are qualified with the function they close over. A bare
    ``post`` says nothing about who is calling out or where it goes;
    ``ollama_chat.post`` is reviewable, and lifting a call into a closure no
    longer silently renames an audited entry into an unaudited one.

    Calls are classified by their *resolved* name: ``client.post`` after
    ``import requests as client`` is audited as ``requests.post``, and a bare
    ``urlopen`` from ``from urllib.request import urlopen`` is audited as
    ``urllib.request.urlopen``. The recorded entry keeps the resolved form, so
    renaming an import cannot move a call out of the audited set.
    """
    tree = ast.parse(path.read_text(encoding="utf-8"))
    aliases = _import_aliases(tree)
    sites: dict[str, set[str]] = {}

    def visit(node: ast.AST, owner: str) -> None:
        for child in ast.iter_child_nodes(node):
            name = owner
            if isinstance(child, (ast.FunctionDef, ast.AsyncFunctionDef)):
                name = (child.name if owner == "<module>"
                        else f"{owner}.{child.name}")
            if isinstance(child, ast.Call):
                dotted = _dotted(child.func)
                if dotted is not None:
                    resolved = _resolve(dotted, aliases)
                    parts = resolved.split(".")
                    if (parts[0] in _CLIENT_CALL_ROOTS
                            or ".".join(parts[:2]) in _CLIENT_CALL_PREFIXES
                            or parts[-1] in _SOCKET_VERBS
                            or resolved in _SERVER_TYPES):
                        sites.setdefault(name, set()).add(resolved)
            visit(child, name)

    visit(tree, "<module>")
    return sites


class DictationPathImportSurfaceTests(unittest.TestCase):
    """No module on the dictation path may hold a network client."""

    def test_dictate_is_the_only_first_party_module_with_a_network_client(self):
        closure = _first_party_closure("dictate")
        # A trivially small closure would make this test vacuous.
        self.assertGreater(len(closure), 30)
        offenders = {
            name: sorted(imports)
            for name, imports in closure.items() if imports
        }
        self.assertEqual(
            sorted(offenders), [AUDITED_NETWORK_MODULE],
            "a module on the dictation path gained the ability to reach the "
            f"network: {offenders}. Speech must not leave the machine; if "
            "this flow is intended, security invariant 7 requires explicit "
            "consent, a privacy-promise update, and a threat-model update "
            "before this list changes.")

    def test_the_runtime_holds_only_the_audited_network_clients(self):
        imports = _network_imports(ROOT / "dictate.py")
        self.assertEqual(
            imports, set(AUDITED_NETWORK_IMPORTS),
            "dictate.py's network client surface changed: "
            f"added {sorted(imports - AUDITED_NETWORK_IMPORTS)}, "
            f"removed {sorted(AUDITED_NETWORK_IMPORTS - imports)}")

    def test_evidence_capture_tooling_cannot_reach_the_network(self):
        # The capture sessions and measurement mode handle raw private
        # evidence: audio, transcripts, and app identity. A network client in
        # any of them would be a direct disclosure path.
        for entry in (
            "capture_app_matrix",
            "capture_delayed_cleanup_cases",
            "capture_lifecycle_evidence",
            "capture_voice_evidence",
            "measurement_mode",
        ):
            with self.subTest(entry=entry):
                closure = _first_party_closure(entry)
                self.assertIn(entry, closure)
                offenders = {
                    name: sorted(imports)
                    for name, imports in closure.items() if imports
                }
                self.assertEqual(
                    offenders, {},
                    f"{entry} can now reach the network through {offenders}; "
                    "it handles private evidence and must not.")


class RuntimeOutboundCallSiteTests(unittest.TestCase):
    """Every socket-capable call site in the runtime stays enumerated."""

    @classmethod
    def setUpClass(cls):
        cls.source = (ROOT / "dictate.py").read_text(encoding="utf-8")
        cls.tree = ast.parse(cls.source)
        cls.sites = _call_sites(ROOT / "dictate.py")

    def test_socket_capable_call_sites_are_exactly_the_audited_set(self):
        self.assertEqual(
            self.sites, {name: set(calls)
                         for name, calls in AUDITED_CALL_SITES.items()},
            "the runtime's socket surface changed. Every entry here has been "
            "reviewed against the privacy promise; a new one has not.")

    def test_the_only_outbound_http_target_is_the_local_model(self):
        # Match by resolved name so `import requests as client` cannot hide a
        # post from this check, and cover every client verb while at it.
        aliases = _import_aliases(self.tree)
        posts = [
            node for node in ast.walk(self.tree)
            if isinstance(node, ast.Call)
            and (dotted := _dotted(node.func)) is not None
            and _resolve(dotted, aliases).startswith("requests.")
        ]
        self.assertTrue(posts)
        for call in posts:
            self.assertTrue(call.args, "requests.post lost its URL argument")
            target = call.args[0]
            self.assertIsInstance(
                target, ast.Name,
                "the outbound URL must stay a reviewed module constant, not "
                "an inline or computed value")
            self.assertEqual(target.id, "OLLAMA_URL")

    def test_the_reviewed_endpoint_constants_stay_local(self):
        constants = {}
        for node in self.tree.body:
            if not isinstance(node, ast.Assign):
                continue
            for assigned in node.targets:
                if isinstance(assigned, ast.Name) and isinstance(
                        node.value, ast.Constant):
                    constants[assigned.id] = node.value.value
        parsed = urllib.parse.urlparse(constants["OLLAMA_URL"])
        self.assertEqual(parsed.scheme, "http")
        self.assertIn(
            (parsed.hostname, parsed.port), ALLOWED_ENDPOINTS,
            f"OLLAMA_URL now points at {parsed.hostname}:{parsed.port}, which "
            "is not a permitted local endpoint")
        self.assertEqual(constants["PHONE_PORT"], 8787)
        self.assertIn(("127.0.0.1", constants["PHONE_PORT"]), ALLOWED_ENDPOINTS)

    def test_route_discovery_transmits_nothing_and_is_server_only(self):
        # lan_ip connects a UDP socket to a public address to learn which
        # local interface would carry LAN traffic. UDP connect only fixes a
        # source address; no datagram is sent. That distinction is the whole
        # reason this call is acceptable, so pin it down.
        function = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef) and node.name == "lan_ip")
        constructed = [
            node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and _dotted(node.func) == "socket.socket"]
        self.assertEqual(len(constructed), 1)
        self.assertEqual(
            [_dotted(argument) for argument in constructed[0].args],
            ["socket.AF_INET", "socket.SOCK_DGRAM"])
        for node in ast.walk(function):
            if isinstance(node, ast.Call):
                leaf = (_dotted(node.func) or "").split(".")[-1]
                self.assertNotIn(
                    leaf, {"send", "sendall", "sendto", "write"},
                    "lan_ip must never transmit on its route-discovery socket")
        self.assertIn("display_host = lan_ip() if SERVER_ONLY else bind_host",
                      self.source)

    def test_the_local_activation_socket_is_unix_domain_only(self):
        function = next(
            node for node in self.tree.body
            if isinstance(node, ast.FunctionDef)
            and node.name == "start_gui_activation_server")
        constructed = [
            node for node in ast.walk(function)
            if isinstance(node, ast.Call)
            and _dotted(node.func) == "socket.socket"]
        self.assertEqual(len(constructed), 1)
        self.assertEqual(
            [_dotted(argument) for argument in constructed[0].args],
            ["socket.AF_UNIX", "socket.SOCK_STREAM"])


class _RecordingResponse:
    """Minimal stand-in for a requests.Response the runtime is happy with."""

    status_code = 200
    text = ""

    def __init__(self, payload):
        self._payload = payload

    def json(self):
        return self._payload

    def raise_for_status(self):
        return None


class EgressViolation(AssertionError):
    """Instrumented code tried to reach an endpoint off the allowlist."""


class EgressTripwire:
    """Record and block every outbound attempt made inside the block.

    Allowed targets are recorded and still blocked: the tests are hermetic and
    must never touch a real socket, not even a local one. ``requests_double``
    is the one exception, and only because the production cleanup function has
    to return a value to be worth testing; it records without blocking, and its
    recorded target is checked against the same allowlist.
    """

    # Socket methods that manage a handle rather than reach anything. Letting
    # these through keeps a real failure legible instead of burying it under a
    # spurious alarm from ``close()``.
    _INERT_SOCKET_METHODS = frozenset({
        "close", "detach", "fileno", "setblocking", "setsockopt",
        "settimeout", "gettimeout", "getsockopt", "shutdown",
    })

    def __init__(self, allowed=ALLOWED_ENDPOINTS):
        self.allowed = frozenset(allowed)
        self.attempts: list[tuple[str, tuple[str, int] | str]] = []
        self._restore: list[tuple[object, str, object]] = []

    # -- recording ------------------------------------------------------
    def _record(self, kind, target):
        normalised = self._normalise(target)
        self.attempts.append((kind, normalised))
        if normalised not in self.allowed:
            raise EgressViolation(
                f"{kind} tried to reach {normalised!r}, which is not one of "
                f"the permitted local endpoints {sorted(self.allowed)}")
        raise EgressViolation(
            f"{kind} reached the allowed endpoint {normalised!r}; these tests "
            "never open a real connection")

    @staticmethod
    def _normalise(target):
        if isinstance(target, str):
            parsed = urllib.parse.urlsplit(target)
            if parsed.scheme and parsed.hostname:
                default = 443 if parsed.scheme == "https" else 80
                return (parsed.hostname, parsed.port or default)
            return (target, 0)
        if isinstance(target, (tuple, list)) and len(target) >= 2:
            return (str(target[0]), int(target[1]))
        return (str(target), 0)

    @property
    def disallowed(self):
        return tuple(
            attempt for attempt in self.attempts
            if attempt[1] not in self.allowed)

    # -- instrumentation ------------------------------------------------
    def _patch(self, module, name, replacement):
        self._restore.append((module, name, getattr(module, name)))
        setattr(module, name, replacement)

    def __enter__(self):
        tripwire = self

        class _Socket:
            def __init__(self, *args, **kwargs):
                self._family = args[0] if args else kwargs.get("family")

            def connect(self, address):
                tripwire._record("socket.connect", address)

            connect_ex = connect

            def sendto(self, _data, address):
                tripwire._record("socket.sendto", address)

            def bind(self, address):
                tripwire._record("socket.bind", address)

            def __getattr__(self, attribute):
                if attribute in tripwire._INERT_SOCKET_METHODS:
                    return lambda *_args, **_kwargs: None

                def _blocked(*_args, **_kwargs):
                    tripwire._record(f"socket.{attribute}", ("unknown", 0))
                return _blocked

        def _create_connection(address, *_args, **_kwargs):
            tripwire._record("socket.create_connection", address)

        def _getaddrinfo(host, port, *_args, **_kwargs):
            tripwire._record("socket.getaddrinfo", (host, port or 0))

        def _gethostbyname(host):
            tripwire._record("socket.gethostbyname", (host, 0))

        def _urlopen(url, *_args, **_kwargs):
            target = getattr(url, "full_url", url)
            tripwire._record("urllib.request.urlopen", target)

        def _http_connection(host, port=None, *_args, **_kwargs):
            tripwire._record("http.client.HTTPConnection", (host, port or 80))

        def _https_connection(host, port=None, *_args, **_kwargs):
            tripwire._record("http.client.HTTPSConnection", (host, port or 443))

        self._patch(socket, "socket", _Socket)
        self._patch(socket, "create_connection", _create_connection)
        self._patch(socket, "getaddrinfo", _getaddrinfo)
        self._patch(socket, "gethostbyname", _gethostbyname)
        self._patch(urllib.request, "urlopen", _urlopen)
        self._patch(http.client, "HTTPConnection", _http_connection)
        self._patch(http.client, "HTTPSConnection", _https_connection)
        return self

    def __exit__(self, *_exception):
        for module, name, original in reversed(self._restore):
            setattr(module, name, original)
        self._restore.clear()
        return False

    def requests_double(self, payload):
        """A ``requests`` stand-in that records the URL it was handed."""
        tripwire = self

        class _Requests:
            @staticmethod
            def post(url, **_kwargs):
                tripwire.attempts.append(
                    ("requests.post", tripwire._normalise(url)))
                return _RecordingResponse(payload)

            @staticmethod
            def get(url, **_kwargs):
                tripwire.attempts.append(
                    ("requests.get", tripwire._normalise(url)))
                return _RecordingResponse(payload)

        return _Requests


def _synthetic_utterance() -> VoiceIR:
    """One made-up dictation with everything the compiler cares about."""
    words = (
        WordEvidence("Send", 0.0, 0.22, 0.91),
        WordEvidence("Priya", 0.22, 0.58, 0.64),
        WordEvidence("the", 0.58, 0.70, 0.95),
        WordEvidence("invoice", 0.70, 1.10, 0.88),
        WordEvidence("for", 1.10, 1.24, 0.93),
        WordEvidence("four", 1.24, 1.52, 0.61),
        WordEvidence("hundred", 1.52, 1.90, 0.86),
        WordEvidence("dollars", 1.90, 2.30, 0.90),
    )
    # A short synthetic waveform: two voiced bursts around a pause. No audio
    # device, no file, no model.
    samples = (
        [0.30, -0.28, 0.31, -0.29] * 240
        + [0.0009, -0.0011] * 480
        + [0.27, -0.26, 0.28, -0.25] * 240
    )
    return VoiceIR(
        hypotheses=(
            RecognitionHypothesis(
                "um Send Priya the invoice for four hundred dollars",
                0.74, "parakeet-unified", words=words),
            RecognitionHypothesis(
                "um Send Pria the invoice for 400 dollars", 0.58, "tiny"),
        ),
        context=ContextRouter().collect(ContextObservation(
            app="Mail", bundle="com.apple.mail",
            field_text="Invoice for Priya Raghavan, due Friday",
        )),
        personal_priors=(PersonalPrior("Pria", "Priya", 4),),
        prosody=analyze_prosody(samples),
        app_bundle="com.apple.mail",
        mode="compose",
        finalized=True,
    )


def _drive_compile_path():
    """Run the local pipeline end to end on the synthetic utterance."""
    voice = _synthetic_utterance()
    compiled = VoiceCompiler().compile(voice)
    plan = build_consequence_plan(voice, audio_duration=2.30)
    receipt = context_firewall_receipt(voice, compiled=compiled)
    cleanup = compile_cleanup(compiled.text)
    anchors = protected_anchors(compiled.text, voice.context.candidates)
    return compiled, plan, receipt, cleanup, anchors


class SyntheticUtteranceEgressTests(unittest.TestCase):
    """A whole dictation may be compiled without a single connection."""

    def test_the_compile_path_opens_no_connection_at_all(self):
        with EgressTripwire() as tripwire:
            compiled, plan, receipt, cleanup, anchors = _drive_compile_path()
        # The pipeline really ran; an exception-swallowing no-op would also
        # produce zero connections.
        self.assertIn("Priya", compiled.text)
        self.assertTrue(plan.risks)
        self.assertTrue(receipt.disposition)
        self.assertNotIn("um ", cleanup.text.lower())
        self.assertIsInstance(anchors, tuple)
        self.assertEqual(
            tripwire.attempts, [],
            "compiling one utterance attempted network I/O: "
            f"{tripwire.attempts}")

    def test_repeated_compilation_stays_silent(self):
        # Caches, lazy imports, and one-shot telemetry all hide on a second
        # pass, so run the pipeline again inside the same tripwire.
        with EgressTripwire() as tripwire:
            for _ in range(3):
                _drive_compile_path()
        self.assertEqual(tripwire.attempts, [])

    def test_cleanup_of_arbitrary_dictated_text_stays_silent(self):
        with EgressTripwire() as tripwire:
            for raw in (
                "new paragraph ship it on Friday",
                "email alex at example dot com scratch that",
                "run git push origin main um please",
                "https://internal.example.com/runbook needs an update",
            ):
                self.assertTrue(compile_cleanup(raw).text)
        self.assertEqual(tripwire.attempts, [])


class ProductionCleanupEgressTests(unittest.TestCase):
    """The one function that does talk to a model talks only to the local one."""

    @classmethod
    def setUpClass(cls):
        cls.tree = ast.parse((ROOT / "dictate.py").read_text(encoding="utf-8"))

    def _load(self, *names, assignments=(), extra=None):
        """Execute selected production definitions without importing the app.

        dictate.py imports AppKit, MLX, and audio frameworks at module scope,
        so the real module cannot be imported in a hermetic test. Compiling the
        selected definitions runs the shipping code rather than a copy of it.
        """
        selected = []
        found = set()
        for node in self.tree.body:
            if isinstance(node, (ast.FunctionDef, ast.ClassDef)) \
                    and node.name in names:
                selected.append(node)
                found.add(node.name)
            elif isinstance(node, ast.Assign):
                targets = {target.id for target in node.targets
                           if isinstance(target, ast.Name)}
                if targets & set(assignments):
                    selected.append(node)
        self.assertEqual(found, set(names), "production definitions missing")
        namespace = dict(extra or {})
        module = ast.fix_missing_locations(ast.Module(
            body=[
                ast.ImportFrom(
                    module="__future__",
                    names=[ast.alias(name="annotations")], level=0),
                *selected,
            ],
            type_ignores=[],
        ))
        exec(compile(module, "dictate-selected", "exec"), namespace)
        return namespace

    def test_the_model_call_targets_only_the_allowlisted_local_endpoint(self):
        import json
        import re

        payload = {
            "message": {"content": '{"text": "Ship it on Friday.", '
                                   '"edits": []}'},
            "done_reason": "stop",
        }
        with EgressTripwire() as tripwire:
            namespace = self._load(
                "ollama_chat",
                assignments=("OLLAMA_URL", "OLLAMA_MODEL"),
                extra={
                    "json": json,
                    "re": re,
                    "requests": tripwire.requests_double(payload),
                },
            )
            text, done = namespace["ollama_chat"](None, "um ship it friday")

        self.assertEqual(text, '{"text": "Ship it on Friday.", "edits": []}')
        self.assertEqual(done, "stop")
        self.assertEqual(len(tripwire.attempts), 1)
        kind, target = tripwire.attempts[0]
        self.assertEqual(kind, "requests.post")
        self.assertIn(
            target, ALLOWED_ENDPOINTS,
            f"cleanup contacted {target!r}, which is not a permitted local "
            "endpoint")
        self.assertEqual(tripwire.disallowed, ())

    def test_a_client_aimed_off_the_allowlist_is_rejected(self):
        # Same production function, same harness, one changed constant. If the
        # local model URL were ever repointed at a hosted service, this is the
        # shape of the failure the allowlist must produce.
        import json
        import re

        with EgressTripwire() as tripwire:
            namespace = self._load(
                "ollama_chat",
                assignments=("OLLAMA_MODEL",),
                extra={
                    "json": json,
                    "re": re,
                    "OLLAMA_URL": "https://cleanup.example.com/api/chat",
                    "requests": tripwire.requests_double(
                        {"message": {"content": "x"}}),
                },
            )
            namespace["ollama_chat"](None, "anything")

        self.assertEqual(
            tripwire.disallowed,
            (("requests.post", ("cleanup.example.com", 443)),))


class EgressTripwireTests(unittest.TestCase):
    """The alarm has to be armed, or the green tests above mean nothing."""

    def test_a_raw_socket_connection_is_caught(self):
        with EgressTripwire() as tripwire:
            with self.assertRaises(EgressViolation):
                probe = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
                probe.connect(("telemetry.example.com", 443))
        self.assertEqual(
            tripwire.disallowed,
            (("socket.connect", ("telemetry.example.com", 443)),))

    def test_create_connection_is_caught(self):
        with EgressTripwire() as tripwire:
            with self.assertRaises(EgressViolation):
                socket.create_connection(("192.0.2.10", 9000))
        self.assertEqual(
            tripwire.disallowed,
            (("socket.create_connection", ("192.0.2.10", 9000)),))

    def test_urlopen_is_caught(self):
        with EgressTripwire() as tripwire:
            with self.assertRaises(EgressViolation):
                urllib.request.urlopen("https://transcripts.example.com/upload")
        self.assertEqual(
            tripwire.disallowed,
            (("urllib.request.urlopen",
              ("transcripts.example.com", 443)),))

    def test_http_client_is_caught(self):
        with EgressTripwire() as tripwire:
            with self.assertRaises(EgressViolation):
                http.client.HTTPSConnection("analytics.example.com")
        self.assertEqual(
            tripwire.disallowed,
            (("http.client.HTTPSConnection", ("analytics.example.com", 443)),))

    def test_name_resolution_alone_is_caught(self):
        # Leaking a hostname through DNS is still a leak, even if the
        # connection never completes.
        with EgressTripwire() as tripwire:
            with self.assertRaises(EgressViolation):
                socket.getaddrinfo("exfil.example.com", 443)
        self.assertEqual(
            tripwire.disallowed,
            (("socket.getaddrinfo", ("exfil.example.com", 443)),))

    def test_an_allowlisted_endpoint_is_recorded_but_never_dialled(self):
        with EgressTripwire() as tripwire:
            with self.assertRaises(EgressViolation):
                socket.create_connection(("127.0.0.1", 11434))
        self.assertEqual(
            tripwire.attempts,
            [("socket.create_connection", ("127.0.0.1", 11434))])
        self.assertEqual(tripwire.disallowed, ())

    def test_the_tripwire_restores_the_standard_library(self):
        originals = {
            "socket.socket": socket.socket,
            "socket.create_connection": socket.create_connection,
            "socket.getaddrinfo": socket.getaddrinfo,
            "socket.gethostbyname": socket.gethostbyname,
            "urllib.request.urlopen": urllib.request.urlopen,
            "http.client.HTTPConnection": http.client.HTTPConnection,
            "http.client.HTTPSConnection": http.client.HTTPSConnection,
        }
        with EgressTripwire():
            self.assertIsNot(socket.socket, originals["socket.socket"])
        current = {
            "socket.socket": socket.socket,
            "socket.create_connection": socket.create_connection,
            "socket.getaddrinfo": socket.getaddrinfo,
            "socket.gethostbyname": socket.gethostbyname,
            "urllib.request.urlopen": urllib.request.urlopen,
            "http.client.HTTPConnection": http.client.HTTPConnection,
            "http.client.HTTPSConnection": http.client.HTTPSConnection,
        }
        self.assertEqual(current, originals)

    def test_the_tripwire_restores_the_standard_library_after_a_violation(self):
        original = socket.socket
        with self.assertRaises(EgressViolation):
            with EgressTripwire():
                socket.create_connection(("exfil.example.com", 443))
        self.assertIs(socket.socket, original)


if __name__ == "__main__":
    unittest.main()
