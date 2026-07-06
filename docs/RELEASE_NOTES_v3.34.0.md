# v3.34.0 — imports for C/C++, Ruby, and Bash

*2026-07-05 · fills the notes gap in the release-notes series (this release
originally shipped with a CHANGELOG entry only) · details: `CHANGELOG.md`*

Three languages modelled calls but not imports, leaving module-level liveness
and `trace_path` chains blind to file loading:

- **C/C++** — `#include "util.h"` now emits an IMPORTS edge to the matching
  module by stem (`LangSpec.import_strings`); system `<...>` headers are
  external by definition and skipped (precision over recall).
- **Ruby** — `require` / `require_relative` with a string argument
  (`LangSpec.import_calls`), path-stemmed to the target module.
- **Bash** — `source lib.sh` and the POSIX `. lib.sh` form, same mechanism.

Also: the `docs/LANGUAGES.md` support matrix corrected to match shipped
behaviour per language.
