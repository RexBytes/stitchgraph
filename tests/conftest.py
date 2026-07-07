"""Suite-wide environment guards.

STITCHGRAPH_NO_LSP: since v3.48.0 `reindex` runs the language-server pass
AUTOMATICALLY when a matching server binary is installed (the best-available-
analysis default). On a dev machine with rust-analyzer / typescript-language-
server present, that would spawn a real server for every tree-sitter fixture
reindex in this suite — hundreds of them. The kill switch pins fixtures to the
name-based graph; the LSP tests opt back in per-test (an explicit `lsp=True`
param outranks the env, and the config-driven tests monkeypatch.delenv)."""
import os

os.environ.setdefault("STITCHGRAPH_NO_LSP", "1")
