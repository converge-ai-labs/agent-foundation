# Native SDK generation

`Service.create_app().openapi()` owns the HTTP contract. The exporter runs without starting the Service lifespan and commits the Native `/api/v1` subset to `sdk/openapi.json`. The TypeScript package also publishes a generated copy of that document for existing consumers.

From the repository root:

```bash
make sdk-generate
make sdk-generated-check
```

Generation exports the current Service schema and replaces only the owned output directories below. The check first compares Service with the committed schema, then generates into temporary directories and compares file names and bytes, including removed files. It never refreshes committed output before checking it. Commit both the contract and generated changes; do not edit generated files.

The local pre-commit hook regenerates on Service/package, schema, generator, or relevant dependency changes. It deliberately does not stage or commit files. SDK CI runs the non-mutating check and routes shared contract changes to every language gate. There is no bot, watcher, daemon, or auto-commit workflow.

| Language   | Pinned generator                                | Owned output                                                  |
| ---------- | ----------------------------------------------- | ------------------------------------------------------------- |
| Python     | openapi-python-client 0.29.1; Ruff 0.16.3       | `sdk/python/a13n/generated/`                                  |
| Go         | oapi-codegen 2.8.0                              | `sdk/go/generated/`                                           |
| Rust       | OpenAPI Generator 7.25.0, reqwest               | `sdk/rust/src/generated/`                                     |
| TypeScript | openapi-typescript, pinned by package-lock.json | `sdk/typescript/src/schema.ts`, `sdk/typescript/openapi.json` |

Use Python 3.13/uv, Go 1.25+, Node.js 24/npm, and the repository Rust toolchain with rustfmt. uv supplies the pinned Rust generator's bundled JDK; a system Java installation is not needed. The generator tools are development dependencies, not SDK runtime dependencies.

## Narrow compatibility adapters

- Python keeps structured attrs models and typed unions, including `UNSET` separately from `None`. Only schema default annotations are removed from the generator input; default HTTP responses and properties named `default` remain. The `httpx2 as httpx` import adapter uses the SDK's existing transport. Binary content-type aliases are generation-only. Raw file uploads use the handwritten async chunk adapter. Generated request builders are public as `build_request`. Model/client/response repr does not print payloads or credentials.
- Go enables `nullable-type` and `skip-prune`. Unions retain generated typed `As...`/`From...` branches rather than application-defined maps. Credential-bearing request formatting is redacted without changing JSON serialization.
- Rust overrides the upstream model template to retain typed `anyOf` branches, double-option nullable fields, boolean constants, and optional null-only fields. An unconstrained JSON branch maps to `serde_json::Value` only when the source already admits arbitrary JSON. JSON operations return typed data plus status/headers; binary operations retain reqwest streaming responses. Template-style Clippy exceptions apply only to generated code; type and correctness checks remain enabled.
- `RunStatus` preserves unknown strings in Python and Rust; closed discriminator tags are not opened indiscriminately. Other generated closed enums and validation constraints are not a promise of full JSON Schema validation. Go and TypeScript preserve unknown response data at runtime.

The existing Search convenience clients remain public. Generated calls share their transport and cancellation through Python/Rust `execute` and Go `API`. Generated low-level results expose their own typed response/error containers; they do not acquire the Search facade's 1 MiB JSON bound or its exception mapping. Binary bodies must be consumed inside the documented stream/lifetime boundary. Run SSE and notification WebSocket recovery remain handwritten protocol work, not generated ordinary HTTP behavior.

## Validation

The four SDK gates compile/type-check generated code, run shared `sdk/fixtures/wire.json` cases and negative type contracts, and exercise transport headers and shutdown. Service's Search tests include a generated Python CRUD/precondition integration against real routes and SQLite storage (with the test authenticator). Generator tests check full operation coverage and stale-file detection. This is not a claim of four-language full protocol parity or validation equivalence for every JSON Schema keyword.

The Rust templates are derived from OpenAPI Generator 7.25.0's Apache-2.0 templates. Keep changes there small and rerun the shared fixtures when upgrading generators.
