# v3.35.0 — the contract-resolvers release

Closes the two remaining "additive resolvers" roadmap rows: the service boundary
gets spec-first coverage (OpenAPI, gRPC), and the ORM family gains its JS/TS
twins (Prisma, TypeORM). All four follow the resolver contract: extra
nodes/edges, INFERRED/AMBIGUOUS provenance recorded per edge, never a mutation.

## OpenAPI / Swagger

A spec file *is* a routing table. Each `paths` entry becomes a ROUTE node on the
**same id convention as the code-first route resolvers** (`{rel}::route:{METHOD}
{path}`), so `<form action>` and JS `fetch` edges converge on spec-defined routes
exactly as they do on decorator-defined ones. `operationId` links the route to
its same-named handler(s) — the reason this matters: a spec-first service wires
handlers by configuration, so nothing in the static call graph ever calls them,
and they were false dead-code candidates. JSON parses with stdlib; YAML needs
pyyaml (now in the default install; guarded import — without it YAML specs are
skipped, never crashed on). Data files are byte-gated (first 200 bytes must
mention `openapi`/`swagger`) so the resolver never parses every YAML in a repo.

## gRPC / protobuf

`rpc` definitions become ROUTE nodes (`{rel}::rpc:{Service}.{Method}`) bound to
the conventional server implementations — `{Service}Servicer` (grpcio),
`{Service}Base` (grpclib), `{Service}Impl` — falling back to service-name-scoped
candidates, never blanket same-name edges. Servicer methods stop surfacing as
dead code, and everything they call becomes reachable through the rpc root. The
parse is a deliberately small brace-matched regex pass — no protobuf dependency.

## Prisma + TypeORM

Both map onto `db::<table>` DBTable nodes, converging with the SQL resolver's
table keys, so a full-stack trace can cross from a TS entity to the raw query
that reads the same table.

- **Prisma**: `model X { … }` in `schema.prisma` (honouring `@@map`); the client
  is generated so there's no in-graph model class — same-named hand-written
  domain classes get the MAPS_TO edge (AMBIGUOUS when several).
- **TypeORM**: `@Entity()` / `@Entity("name")` classes in TS/TSX; the class node
  comes from the tree-sitter extractor, this resolver adds only the table
  mapping. Byte-gated on `@Entity` so it never regexes every TS file.

## Tests

Three new framework tests: the OpenAPI spec (YAML + JSON, with and without
operationId) roots its handlers out of the dead list; the proto servicer binding
does the same and reaches through to the servicer's callees; Prisma `@@map` +
TypeORM entity land on the right `db::` keys with the right MAPS_TO sources.
Full suite green.
