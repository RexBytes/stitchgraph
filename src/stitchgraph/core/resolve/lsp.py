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


def any_server_available(overrides: dict[str, str] | None = None) -> bool:
    """True when at least one registered language-server binary is on PATH —
    the AUTO gate (v3.48.0): the best available analysis runs by default, and
    a machine with no servers skips the pass without ever spawning anything."""
    cmds = {cmd for cmd, _lang in DEFAULT_SERVERS.values()}
    for ext, cmd in (overrides or {}).items():
        if cmd.strip():
            cmds.add(cmd)
        else:
            default = DEFAULT_SERVERS.get(ext)
            if default:
                cmds.discard(default[0])
    return any(shutil.which(cmd.split()[0]) is not None for cmd in cmds if cmd)


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
        self._lock = threading.Lock()
        self._opened: set[str] = set()

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
        flips to the true definition ~2 s in). Best-effort: on deadline the
        pass proceeds with whatever the server gives."""
        prev: list | None = None
        t0 = time.monotonic()
        while time.monotonic() - t0 < deadline:
            cur = self.definition(rel, line, char)
            if cur and prev == cur:
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
                    return  # framing lost: stop reading, requests will time out
                while True:  # consume remaining headers up to the blank line
                    sep = proc.stdout.readline()
                    if not sep or sep in (b"\r\n", b"\n"):
                        break
                body = proc.stdout.read(n)
                if len(body) != n:
                    return
                try:
                    msg = json.loads(body)
                except ValueError:
                    continue  # one garbage frame doesn't kill the session
                if isinstance(msg, dict) and "id" in msg and (
                        "result" in msg or "error" in msg):
                    self._pending[msg["id"]] = msg
                # notifications / server->client requests are dropped: this
                # client never registers capabilities that require answering
        except (OSError, ValueError):
            return

    def _wait(self, rid: int, timeout: float) -> dict | None:
        t0 = time.monotonic()
        while rid not in self._pending:
            if time.monotonic() - t0 > timeout or not self.available:
                return None
            time.sleep(0.02)
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
