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

## Opt-in Target Transport

Targets default to unsupported. A trusted tool can set metadata `a13n.harness.supports_object_refs: true`. `ObjectRefToolset` supplies a toolset default with individual native metadata and explicit `tool_overrides` taking precedence. Host MCP configuration supplies an equivalent server default and per-tool overrides. Opt-in is appropriate only for targets whose string arguments accept base64.

The schema advertises exact reference values alongside constrained strings. Native Pydantic Core validators preserve non-reference validation, defaults, coercion and typed arguments. Reference-valued string constraints and enclosing field/model callbacks whose input contains references defer payload checks until effective arguments are available; native inner fields still validate. Payload-dependent cross-field checks belong to the enclosing model validator, not an unrelated field's callback. Opted-in custom toolsets require a native Core argument validator; an unsupported validator fails explicitly during composition. The pinned native compatibility adapter preserves structural argument normalization and is covered by dependency-compatibility tests.

A custom tool `args_validator` also runs on the reference-valued typed arguments before any Store read, preserving its business approval, deferral and denial semantics. Its errors propagate normally. A successful reference call runs it again on effective arguments after expansion. Opted-in callbacks must accept reference-valued preflight arguments and repeated validation; payload checks can use the native field/model validators above. The transport never suppresses a callback failure to reach the bytes.

The actual target dispatch boundary recursively expands whole reference-valued strings in dictionaries, lists and tuples. Dictionary keys, substrings, CodeAct program source and forwarded proxy envelopes are unchanged. Each distinct reference is read once per call, base64-encoded from those bytes, and substituted into a call-local argument copy. The target's original schema and custom argument validator run on the effective arguments before execution. The transport never substitutes a raw-dictionary validation fallback.

[Tool permissions](07-tool-execution.md#tool-permissions-and-review), resource resolution and approvals observe reference-valued arguments before Store reads. Denial causes no read. Approval resume resolves at the actual target, under current Host authority. Direct calls, grouped ToolSearch dispatch and CodeAct nested calls use the same target boundary. Expanded arguments do not replace tool-call history, approval requests or CodeAct source.

## Failure Semantics

Disabled Capability or missing Store and unsupported targets produce distinct developer-action diagnostics before target execution. Invalid, missing, expired, deleted and evicted references fail without guessing IDs, inventing base64 or replaying a producer. Effective-schema and custom-validator errors are sanitized so they do not echo expanded payloads. Host authority and storage failures retain their owning execution semantics.

A successful import establishes availability in the Host catalog; model-history persistence and long-term promotion are independent completions. Cancellation or unknown backend writes remain the Host's cleanup responsibility. A copied reference does not transfer scope into another Thread or Host.

## Invariants

- Base64 decoded by a consumer equals the original imported bytes.
- Native media remains usable by the Model after capture.
- Reference permission denial and pending approval perform no Store read.
- Each reference is read at most once per actual target call.
- Promotion preserves object identity and excludes it from temporary retention.
- Reference-valued history survives approval/resume without storing expanded call arguments.
- Store scope and current execution authority remain Host-owned on every operation.
