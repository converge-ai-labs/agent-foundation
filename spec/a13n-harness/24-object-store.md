# Object Store

## Design Position

`ObjectStoreCapability` lets an Agent reuse original binary bytes across tool calls through compact references. Harness owns capture, management tools, reference-aware schemas and target-dispatch hooks. A Host implements `ObjectStore` and owns scope, authorization, persistence, quotas, expiry and cleanup. Harness imports no Host storage implementation.

A reference is a transport value, not an execution-content reference, an Environment path, a public URL or a workspace Asset. It identifies immutable bytes inside the Store bound to the current Run. Knowing an ID grants no access.

## Binding and Catalog

Select `ObjectStoreCapability` in the definition and supply `RunBindings.object_store`. Missing dependencies and orphan bindings fail at the existing Capability contract boundary. The public protocol and value types live in `a13n_harness.objects`; Capability types live in `a13n_harness.capabilities`.

The conceptual Store contract is:

```python
# Conceptual async Python interface, not a wire schema.
put(data: bytes, *, media_type: str, name: str | None = None) -> ObjectInfo
read(object_id: str) -> bytes
info(object_id: str) -> ObjectInfo
store(object_id: str) -> ObjectInfo
list(*, offset: int = 0, limit: int = 50) -> list[ObjectInfo]
delete(object_id: str) -> None
```

`ObjectInfo` contains `object_id`, `media_type`, byte `size`, SHA-256 `digest`, optional `name` and `retention` (`temporary` or `stored`). Its presentation adds `ref`, encoded as `a13n-object://obj_<opaque-id>`. Metadata contains no bytes or base64. IDs are immutable and never reassigned to different content.

Imports enter the temporary tier. A Host bounds temporary retention by idle TTL, least-recently-used count and total bytes. An actual byte read refreshes access; `info` and `list` do not. `store` promotes an available entry idempotently, preserving its ID, digest and bytes. Stored entries are excluded from temporary TTL and LRU quotas. Promotion cannot resurrect an expired, evicted or deleted object. Host-specific defaults and physical storage belong to the Host contracts.

A completed read owns its call-local bytes independently of later deletion or eviction. There are no approval-lifetime pins: a reference may expire while a call awaits approval. Persisting history preserves the reference, not object availability.

## Capture and Management

When `capture_media` is enabled, the Capability captures native binary user inputs and binary tool results, including native MCP media converted by the upstream adapter. It adds compact metadata beside the original content and preserves native vision/audio projection. Automatic capture never publishes a workspace Asset or downloads an arbitrary URL. The Capability's `max_object_bytes` bounds capture and explicit file imports; Hosts may impose smaller limits. A Host size rejection during additive capture reports unavailable capture while preserving the native result. Explicit imports report the size failure.

The Capability contributes five tools:

| Tool                             | Behavior                                                                          |
| -------------------------------- | --------------------------------------------------------------------------------- |
| `asset_ref(path, media_type?)`   | Snapshot an authorized regular Environment file into temporary storage.           |
| `asset_store(ref?, path?)`       | Promote a reference, or import and promote a file; exactly one input is required. |
| `asset_info(ref)`                | Return payload-free metadata.                                                     |
| `asset_list(offset=0, limit=50)` | Return a bounded page, with a maximum limit of 100.                               |
| `asset_delete(ref)`              | Remove the entry from future lookup.                                              |

File import resolves one authorized Environment route, opens that selection, checks regular-file metadata, and bounds the byte stream. It does not reinterpret a path as a Host path or bypass mount permissions. Management tools consume references directly and never expand their own arguments.

## Object Reference Transport

Object Reference Transport is the Harness tool-extension contract for substituting scoped object references with base64 at target dispatch. Object Store owns byte availability; this extension owns how an opted-in tool receives those bytes.

Targets default to unsupported. A trusted tool can set boolean metadata `a13n.harness.supports_object_refs: true`. `ObjectRefToolset` and Host MCP configuration use the same `object_ref_tools` map from tool-name patterns to booleans. The map defaults to empty; `{"*": true}` enables a whole toolset or server. Explicit matching rules override native metadata; unmatched tools retain their native declaration, defaulting to false. Opt-in is appropriate only for targets whose string arguments accept base64.

Rules match original, case-sensitive tool names before Host or ToolSearch prefixes. `*` matches zero or more characters anywhere, including suffixes (`*_upload`) and multiple positions (`image_*_upload`); all other characters are literal. An exact tool-name entry wins over patterns. Among matching patterns, the one with more literal (non-`*`) characters wins; equally specific patterns prefer false. Mapping order does not affect selection. `"*"` therefore supplies the least-specific default, and exact entries express exceptions. This selects transport compatibility only; permission and approval policies remain independent.

The schema advertises exact reference values alongside constrained strings. Native Pydantic Core validation admits exact references at declarative string constraints and preserves defaults, coercion, typed arguments and unrelated constraints. Field, container and model callbacks run normally on reference-valued inputs; integrations must accept and preserve reference tokens where they accept this transport. Opted-in custom toolsets require a native Core argument validator; an unsupported validator fails explicitly during composition. The pinned native compatibility adapter preserves structural argument normalization and is covered by dependency-compatibility tests.

Native argument validation, custom tool `args_validator` callbacks and before/after-validation hooks run once through the ordinary invocation pipeline, before any Store read. Business approval, deferral, denial and callback failures retain their native semantics. The tool implementation or remote MCP server owns payload-dependent checks on the bytes it receives.

The actual target dispatch boundary recursively replaces whole reference-valued strings in dictionaries, lists, tuples and typed models in the final authorized arguments. Dictionary keys, substrings, CodeAct program source and forwarded proxy envelopes are unchanged. Each distinct remaining reference is read once per call, base64-encoded from those bytes, and substituted into a call-local argument copy that preserves native model types. References removed by validation hooks are not read. Dispatch performs substitution only: it does not rerun schemas, callbacks, default factories or hooks. Normalized values and hook rewrites reach the consumer unchanged apart from reference substitution.

[Tool permissions](07-tool-execution.md#tool-permissions-and-review), resource resolution and approvals observe reference-valued arguments before Store reads. Denial causes no read. Approval resume resolves at the actual target, under current Host authority. Direct calls, grouped ToolSearch dispatch and CodeAct nested calls use the same target boundary. Expanded arguments do not replace tool-call history, approval requests or CodeAct source.

## Failure Semantics

Disabled Capability or missing Store and unsupported targets produce distinct developer-action diagnostics before target execution. Invalid, missing, expired, deleted and evicted references fail without guessing IDs, inventing base64 or replaying a producer. Pre-dispatch validation sees references rather than expanded payloads; consumers own their payload-validation errors. Host authority and storage failures retain their owning execution semantics.

A successful import establishes availability in the Host catalog; model-history persistence and long-term promotion are independent completions. Cancellation or unknown backend writes remain the Host's cleanup responsibility. A copied reference does not transfer scope into another Thread or Host.

## Invariants

- Base64 decoded by a consumer equals the original imported bytes.
- Native media remains usable by the Model after capture.
- Reference permission denial and pending approval perform no Store read.
- Each reference is read at most once per actual target call.
- Argument validation and hooks are not replayed after substitution; non-reference values retain their final validated or hook-rewritten meaning.
- Promotion preserves object identity and excludes it from temporary retention.
- Reference-valued history survives approval/resume without storing expanded call arguments.
- Store scope and current execution authority remain Host-owned on every operation.
