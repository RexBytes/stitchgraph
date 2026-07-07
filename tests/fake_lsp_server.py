"""A deterministic fake language server for the LSP client/resolver tests.

Speaks real JSON-RPC over stdio (Content-Length framing) so the client's
transport, handshake, and normalisation are exercised for real — without any
external binary. Behaviour is scripted by a JSON answers file:

    {
      "shape": "location" | "location_list" | "link_list",
      "definitions": {"<rel>:<line>:<char>": {"rel": ..., "line": ..., "char": ...}},
      "hover": {"<rel>:<line>:<char>": "type text"},
      "mode": "normal" | "mute" | "garbage" | "die"
    }

Positions are the LSP wire values (0-based line). Modes: "mute" answers
initialize but never definitions (timeout path); "garbage" prints junk and
exits (framing-loss path); "die" exits before reading anything (dead-shim
path). Usage: python fake_lsp_server.py <answers.json>
"""
import json
import sys
from pathlib import Path

spec = json.loads(Path(sys.argv[1]).read_text())
mode = spec.get("mode", "normal")
if mode == "die":
    sys.exit(1)
if mode == "garbage":
    sys.stdout.write("this is not a JSON-RPC frame\n" * 5)
    sys.stdout.flush()
    sys.exit(0)

stdin = sys.stdin.buffer
stdout = sys.stdout.buffer


def send(msg):
    raw = json.dumps(msg).encode()
    stdout.write(b"Content-Length: %d\r\n\r\n%s" % (len(raw), raw))
    stdout.flush()


def key(params):
    uri = params["textDocument"]["uri"]
    pos = params["position"]
    # match on the path's tail so the answers file can use project-relative keys
    for k in list(spec.get("definitions", {})) + list(spec.get("hover", {})):
        rel, line, char = k.rsplit(":", 2)
        if uri.endswith(rel) and pos["line"] == int(line) \
                and pos["character"] == int(char):
            return k
    return None


def loc(entry, root_uri):
    uri = f"{root_uri}/{entry['rel']}"
    rng = {"start": {"line": entry["line"], "character": entry["char"]},
           "end": {"line": entry["line"], "character": entry["char"] + 1}}
    if spec.get("shape") == "link_list":
        return {"targetUri": uri, "targetSelectionRange": rng,
                "targetRange": rng, "originSelectionRange": rng}
    return {"uri": uri, "range": rng}


root_uri = ""
while True:
    header = stdin.readline()
    if not header:
        sys.exit(0)
    if not header.lower().startswith(b"content-length:"):
        continue
    n = int(header.split(b":")[1])
    while True:
        sep = stdin.readline()
        if not sep or sep in (b"\r\n", b"\n"):
            break
    msg = json.loads(stdin.read(n))
    method, rid = msg.get("method"), msg.get("id")
    if method == "initialize":
        root_uri = (msg["params"].get("rootUri") or "").rstrip("/")
        send({"jsonrpc": "2.0", "id": rid, "result": {"capabilities": {
            "definitionProvider": True, "hoverProvider": True}}})
    elif method == "textDocument/definition" and rid is not None:
        if mode == "mute":
            continue  # never answer: the client's timeout path
        k = key(msg["params"])
        entry = spec.get("definitions", {}).get(k) if k else None
        if entry is None:
            result = []
        elif spec.get("shape") == "location":
            result = loc(entry, root_uri)          # bare Location, not a list
        else:
            result = [loc(entry, root_uri)]
        send({"jsonrpc": "2.0", "id": rid, "result": result})
    elif method == "textDocument/hover" and rid is not None:
        k = key(msg["params"])
        text = spec.get("hover", {}).get(k) if k else None
        result = {"contents": {"kind": "plaintext", "value": text}} if text else None
        send({"jsonrpc": "2.0", "id": rid, "result": result})
    elif method == "shutdown":
        send({"jsonrpc": "2.0", "id": rid, "result": None})
    elif method == "exit":
        sys.exit(0)
    elif rid is not None:  # any other request: honest empty
        send({"jsonrpc": "2.0", "id": rid, "result": None})
