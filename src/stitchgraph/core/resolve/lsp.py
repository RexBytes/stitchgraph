"""Minimal LSP client + server registry (design §5, research/24).

What jedi is to Python, a language server is to everything else: this module
speaks just enough JSON-RPC-over-stdio to ask `textDocument/definition` and
`textDocument/hover`, so the LspResolver can upgrade name-based tree-sitter
call edges to type-grade EXTRACTED ones. Stdlib only — the ONLY external
requirement is the server binary itself, and a missing/broken/slow server
degrades to "no extra edges", never to an error in the pipeline.

Probe-driven hardening (research/24):
- A server may answer definitions WRONG (import binding) until its project
  load finishes — `warm_up` re-issues one query until two consecutive answers
  agree, instead of trusting the first response or sleeping blind.
- The binary on PATH may be a dead shim (rustup's uninstalled rust-analyzer
  proxy) that exits on first write — every pipe write/read is guarded and a
  dead process just means `available == False`.
- Servers interleave unsolicited notifications (diagnostics, progress, logs)
  with responses — the reader thread routes strictly by `id` presence.
- Result shapes vary (`Location | Location[] | LocationLink[]`); the client
  never advertises `linkSupport` and normalises all three anyway.
"""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import threading
import time
from pathlib import Path

# extension -> (command argv, languageId). Only consulted when the binary
# exists on PATH; stitchgraph.toml [lsp.servers] overrides/extends/disables.
DEFAULT_SERVERS: dict[str, tuple[str, str]] = {
    ".ts": ("typescript-language-server --stdio", "typescript"),
    ".tsx": ("typescript-language-server --stdio", "typescriptreact"),
    ".js": ("typescript-language-server --stdio", "javascript"),
    ".jsx": ("typescript-language-server --stdio", "javascriptreact"),
    ".mjs": ("typescript-language-server --stdio", "javascript"),
    ".cjs": ("typescript-language-server --stdio", "javascript"),
    ".rs": ("rust-analyzer", "rust"),
    ".go": ("gopls", "go"),
    ".c": ("clangd", "c"),
    ".h": ("clangd", "c"),
    ".cpp": ("clangd", "cpp"),
    ".cc": ("clangd", "cpp"),
    ".hpp": ("clangd", "cpp"),
}

_INIT_TIMEOUT = 30.0
_READY_TIMEOUT = 30.0

# One actionable install line per default server binary — the diagnostic a
# consumer can EXECUTE, not just read (field review 2026-07-09, request 1:
# "server unavailable" cost a whole low-confidence first pass that one message
# would have saved).
_INSTALL_HINTS = {
    "rust-analyzer": "rustup component add rust-analyzer",
    "typescript-language-server":
        "npm install -g typescript-language-server typescript",
    "gopls": "go install golang.org/x/tools/gopls@latest",
    "clangd": "install clangd via your system package manager (e.g. apt install clangd)",
}

# rustup proxy errors name the exact command to run — lift it verbatim.
_RUSTUP_ADD = re.compile(r"rustup +component +add +[\w-]+")


# diagnose_server memo: a broken binary's verdict cannot change within a session
# (fixing it makes start() succeed, so diagnose stops being called at all), and
# watch mode constructs a fresh LspResolver per edit — without the cache a dead
# shim would cost a failed spawn PLUS an up-to-10s probe on every save.
_DIAGNOSIS_CACHE: dict[tuple[str, str | None], tuple[str, bool]] = {}


def diagnose_server(cmd: str) -> tuple[str, bool]:
    """Why `cmd` could not serve, as (actionable message, binary_on_path).

    Called only AFTER a start()/initialize failure — it never runs for healthy
    servers. Distinguishes the three field cases that "server unavailable"
    collapsed together: (a) binary not on PATH (with the install hint);
    (b) binary on PATH but a dead proxy shim — rustup's uninstalled
    rust-analyzer proxy errors on launch and names the `rustup component add`
    fix, which is surfaced verbatim; (c) binary present and runnable but the
    LSP initialize handshake failed. Total: any probe failure degrades to a
    generic-but-honest message, never an exception. Memoized per (cmd,
    resolved path) for the session."""
    argv = cmd.split()
    binary = argv[0] if argv else cmd
    located = shutil.which(binary)
    key = (cmd, located)
    cached = _DIAGNOSIS_CACHE.get(key)
    if cached is not None:
        return cached
    _DIAGNOSIS_CACHE[key] = out = _diagnose_uncached(binary, located)
    return out


def _diagnose_uncached(binary: str, located: str | None) -> tuple[str, bool]:
    if located is None:
        hint = _INSTALL_HINTS.get(binary)
        msg = f"'{binary}' is not on PATH"
        return (f"{msg}; install it: {hint}" if hint else msg), False
    try:
        # stdin MUST be detached: this probe targets exactly the population of
        # misbehaving binaries, and one that ignores argv and reads stdin would
        # otherwise inherit the parent's — which is the live MCP JSON-RPC
        # channel when stitchgraph-mcp runs on stdio transport, so it could eat
        # protocol bytes before the timeout kills it (self-review 2026-07-09;
        # LspClient.start() has always piped stdin for the same reason).
        probe = subprocess.run([binary, "--version"], capture_output=True,
                               text=True, timeout=10,
                               stdin=subprocess.DEVNULL)
        err = ((probe.stderr or "") + "\n" + (probe.stdout or "")).strip()
        if probe.returncode != 0:
            fix = _RUSTUP_ADD.search(err)
            if fix:  # the rustup proxy shim: on PATH, but the component isn't installed
                return (f"'{binary}' on PATH is a rustup proxy shim but the component "
                        f"is not installed; run: {fix.group(0)}"), True
            first = err.splitlines()[0] if err else "no error output"
            hint = _INSTALL_HINTS.get(binary)
            # Honest about the probe's limits: a non-zero exit may just mean the
            # server has no --version flag while the REAL failure was the LSP
            # handshake — assert the observation, not a broken install.
            return (f"'{binary}' is on PATH but did not serve; probing it with "
                    f"--version also failed ({first})"
                    + (f" — if it is broken, reinstall: {hint}" if hint else "")), True
    except (OSError, subprocess.TimeoutExpired, ValueError):
        return f"'{binary}' is on PATH but could not be executed", True
    return (f"'{binary}' is installed but did not complete the LSP initialize "
            "handshake"), True


def any_server_available(overrides: dict[str, str] | None = None) -> bool:
    """True when at least one registered language-server binary is on PATH —
    the AUTO gate (v3.48.0): the best available analysis runs by default, and
    a machine with no servers skips the pass without ever spawning anything.

    Computed per EXTENSION, not per command (self-audit 2026-07-07, finding
    4): six extensions share the typescript-language-server command, so
    disabling `.ts` alone must not silently kill AUTO for `.js` siblings."""
    cmds: set[str] = set()
    for ext, (default_cmd, _lang) in DEFAULT_SERVERS.items():
        eff = (overrides or {}).get(ext, default_cmd)
        if eff and eff.strip():
            cmds.add(eff)
    for ext, cmd in (overrides or {}).items():
        if ext not in DEFAULT_SERVERS and cmd.strip():
            cmds.add(cmd)
    return any(shutil.which(cmd.split()[0]) is not None for cmd in cmds)


def server_for(ext: str, overrides: dict[str, str] | None = None) -> tuple[str, str] | None:
    """(command, languageId) for a file extension, or None. An override maps
    extension -> command string; empty string disables that extension."""
    if overrides and ext in overrides:
        cmd = overrides[ext]
        if not cmd.strip():
            return None
        default = DEFAULT_SERVERS.get(ext)
        return cmd, default[1] if default else ext.lstrip(".")
    return DEFAULT_SERVERS.get(ext)


class LspClient:
    """One language server over stdio. Every public method is total: failures
    return None/[]/False — nothing raises past this class."""

    def __init__(self, cmd: str, root: str | Path, timeout: float = 15.0,
                 init_timeout: float = _INIT_TIMEOUT) -> None:
        self.cmd = cmd
        self.root = Path(root).resolve()
        self.timeout = timeout
        self.init_timeout = init_timeout
        self.proc: subprocess.Popen | None = None
        self._id = 0
        self._pending: dict[int, dict] = {}
        self._abandoned: set[int] = set()  # timed-out rids: late replies dropped
        self._lock = threading.Lock()
        self._opened: set[str] = set()
        # consecutive full-timeout requests — the resolver's circuit-breaker
        # signal for an alive-but-SILENT server (self-audit 2026-07-07: a dead
        # process fails fast, a mute one would otherwise cost the full timeout
        # per site × up to 20k sites). Reset on any answered request.
        self.consecutive_timeouts = 0

    # -- lifecycle -------------------------------------------------------
    @property
    def available(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def start(self) -> bool:
        argv = self.cmd.split()
        if not argv or shutil.which(argv[0]) is None:
            return False
        try:
            self.proc = subprocess.Popen(
                argv, stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, cwd=str(self.root))
        except OSError:
            self.proc = None
            return False
        threading.Thread(target=self._reader, daemon=True).start()
        uri = self.root.as_uri()
        rid = self._send("initialize", {
            "processId": os.getpid(), "rootUri": uri,
            "workspaceFolders": [{"uri": uri, "name": self.root.name}],
            "capabilities": {"textDocument": {
                "definition": {}, "hover": {"contentFormat": ["plaintext", "markdown"]}}},
        })
        if rid is None or self._wait(rid, self.init_timeout) is None:
            self.stop()
            return False
        self._notify("initialized", {})
        return self.available

    def stop(self) -> None:
        if self.proc is None:
            return
        try:
            rid = self._send("shutdown", {})
            if rid is not None:
                self._wait(rid, 5.0)
            self._notify("exit", {})
            self.proc.wait(timeout=5.0)
        except (OSError, subprocess.TimeoutExpired):
            pass
        finally:
            if self.proc.poll() is None:  # a hung server never outlives the pass
                try:
                    self.proc.kill()
                except OSError:
                    pass
            self.proc = None

    # -- requests --------------------------------------------------------
    def did_open(self, rel: str, language_id: str) -> bool:
        if rel in self._opened:
            return True
        path = self.root / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            return False
        ok = self._notify("textDocument/didOpen", {"textDocument": {
            "uri": path.as_uri(), "languageId": language_id,
            "version": 1, "text": text}})
        if ok:
            self._opened.add(rel)
        return ok

    def definition(self, rel: str, line: int, char: int) -> list[tuple[str, int, int]]:
        """Definitions of the symbol at (1-based line, 0-based char), as
        project-relative (rel, 1-based line, 0-based char); locations outside
        the root are dropped (stdlib / third-party — the jedi parity rule)."""
        rid = self._send("textDocument/definition", self._pos(rel, line, char))
        resp = self._wait(rid, self.timeout) if rid is not None else None
        out: list[tuple[str, int, int]] = []
        for loc in _locations(resp):
            uri, rng = loc.get("targetUri") or loc.get("uri"), \
                loc.get("targetSelectionRange") or loc.get("range")
            if not uri or not isinstance(rng, dict):
                continue
            try:
                p = Path(_from_uri(uri)).resolve().relative_to(self.root)
            except (ValueError, OSError):
                continue
            start = rng.get("start", {})
            out.append((p.as_posix(), int(start.get("line", 0)) + 1,
                        int(start.get("character", 0))))
        return out

    def hover(self, rel: str, line: int, char: int) -> str | None:
        rid = self._send("textDocument/hover", self._pos(rel, line, char))
        resp = self._wait(rid, self.timeout) if rid is not None else None
        result = (resp or {}).get("result")
        if not isinstance(result, dict):
            return None
        contents = result.get("contents")
        if isinstance(contents, dict):
            value = contents.get("value")
            return value if isinstance(value, str) and value.strip() else None
        if isinstance(contents, str):
            return contents if contents.strip() else None
        if isinstance(contents, list):  # MarkedString[]
            parts = [c if isinstance(c, str) else c.get("value", "")
                     for c in contents if c]
            joined = "\n".join(p for p in parts if p)
            return joined if joined.strip() else None
        return None

    def warm_up(self, rel: str, line: int, char: int,
                deadline: float = _READY_TIMEOUT) -> None:
        """Project loads are asynchronous and a server may answer WRONG (the
        import binding) meanwhile — re-issue one representative query until two
        consecutive answers agree (research/24 probe: typescript-language-server
        flips to the true definition ~2 s in). A run of consecutive EMPTY
        answers also counts as ready (self-audit 2026-07-07, finding 3: a
        fast, healthy server whose honest answer at this position is "nothing
        in-root" — keywords, third-party targets — would otherwise burn the
        whole deadline on every call). Best-effort: on deadline the pass
        proceeds with whatever the server gives."""
        prev: list | None = None
        empties = 0
        t0 = time.monotonic()
        while time.monotonic() - t0 < deadline:
            cur = self.definition(rel, line, char)
            if cur and prev == cur:
                return
            empties = empties + 1 if not cur else 0
            if empties >= 4:  # ~2 s of stable "no answer": the answer IS empty
                return
            prev = cur if cur else None
            time.sleep(0.5)

    # -- plumbing ---------------------------------------------------------
    def _pos(self, rel: str, line: int, char: int) -> dict:
        return {"textDocument": {"uri": (self.root / rel).as_uri()},
                "position": {"line": line - 1, "character": char}}

    def _send(self, method: str, params: dict) -> int | None:
        with self._lock:
            self._id += 1
            rid = self._id
        return rid if self._write({"jsonrpc": "2.0", "id": rid,
                                   "method": method, "params": params}) else None

    def _notify(self, method: str, params: dict) -> bool:
        return self._write({"jsonrpc": "2.0", "method": method, "params": params})

    def _write(self, msg: dict) -> bool:
        if not self.available:
            return False
        raw = json.dumps(msg).encode()
        try:
            assert self.proc is not None and self.proc.stdin is not None
            self.proc.stdin.write(b"Content-Length: %d\r\n\r\n%s" % (len(raw), raw))
            self.proc.stdin.flush()
            return True
        except (OSError, ValueError, AssertionError):  # dead shim / closed pipe
            return False

    def _reader(self) -> None:
        proc = self.proc
        if proc is None or proc.stdout is None:
            return
        try:
            while True:
                header = proc.stdout.readline()
                if not header:
                    return
                if not header.lower().startswith(b"content-length:"):
                    continue  # unknown header or stray output line
                try:
                    n = int(header.split(b":")[1])
                except (ValueError, IndexError):
                    # Framing lost: without a length we can never resync. Kill
                    # the process so `available` flips False and every waiter
                    # fails FAST — leaving it alive turned one glitch into a
                    # mute server paying full timeout per site (self-audit
                    # 2026-07-07, finding 1).
                    self._kill()
                    return
                while True:  # consume remaining headers up to the blank line
                    sep = proc.stdout.readline()
                    if not sep or sep in (b"\r\n", b"\n"):
                        break
                body = proc.stdout.read(n)
                if len(body) != n:
                    self._kill()
                    return
                try:
                    msg = json.loads(body)
                except ValueError:
                    continue  # one garbage frame doesn't kill the session
                if not isinstance(msg, dict):
                    continue
                if "id" in msg and ("result" in msg or "error" in msg):
                    if msg["id"] in self._abandoned:
                        self._abandoned.discard(msg["id"])  # late reply: drop
                    else:
                        self._pending[msg["id"]] = msg
                elif "id" in msg and "method" in msg:
                    # a server->client REQUEST (workspace/configuration,
                    # client/registerCapability, ...): refuse politely instead
                    # of silently dropping — a server that synchronously
                    # awaits the reply would otherwise go mute (finding 6)
                    self._write({"jsonrpc": "2.0", "id": msg["id"],
                                 "error": {"code": -32601,
                                           "message": "method not supported"}})
                # notifications are dropped
        except (OSError, ValueError):
            return

    def _kill(self) -> None:
        try:
            if self.proc is not None and self.proc.poll() is None:
                self.proc.kill()
        except OSError:
            pass

    def _wait(self, rid: int, timeout: float) -> dict | None:
        t0 = time.monotonic()
        while rid not in self._pending:
            if not self.available:
                return None  # dead process: fast fail, not a "timeout"
            if time.monotonic() - t0 > timeout:
                self._abandoned.add(rid)  # a late reply must not leak/pend
                self.consecutive_timeouts += 1
                return None
            time.sleep(0.02)
        self.consecutive_timeouts = 0
        return self._pending.pop(rid)


def _locations(resp: dict | None) -> list[dict]:
    """Normalise Location | Location[] | LocationLink[] to a list of dicts."""
    result = (resp or {}).get("result")
    if isinstance(result, dict):
        return [result]
    if isinstance(result, list):
        return [r for r in result if isinstance(r, dict)]
    return []


def _from_uri(uri: str) -> str:
    from urllib.parse import unquote, urlparse
    return unquote(urlparse(uri).path)
