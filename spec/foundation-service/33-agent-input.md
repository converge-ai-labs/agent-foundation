# Agent Input

## Design Position

`AgentInput` is Foundation's versioned JSON submission and accepted-value protocol for one unit of ordinary semantic Agent input. Root invocation, continuation, queued submission, fork, active-Run steering, managed Trigger acceptance, and input submitted to an asynchronous child use the same protocol. A result returning from that child uses the distinct Host-owned [`AsyncSubagentResultInboxPayload`](18-async-subagents.md#asynchronous-child-runs), preserving Agent provenance instead of impersonating caller input or waiting feedback. Control preconditions, AgentPreset Revision selection, typed config override, authorization, and trigger metadata remain outside `AgentInput`.

This contract owns content blocks, structured content, binary acquisition and delivery, accepted canonicalization, input adapter configuration, and deterministic mapping to the Harness native input boundary. [Agent Control: Input and Continuation](34-agent-control-input-and-continuation.md) owns Run acceptance, lineage, retry, and deferred feedback; [Agent Control: Active Execution](35-agent-control-active-execution.md) owns durable steering through the Thread inbox and interrupt of already accepted work; [Agent Control: Queued Submissions](36-agent-control-queued-submissions.md) owns editable future input before Run acceptance. None defines another ordinary input protocol.

## Boundaries

| Concern                                            | Owner                                                                                                                                                       | Relationship                                                                                                                                               |
| -------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Input wire, blocks, sources, delivery, and adapter | This contract and [Agent Management](12-agent-management.md)                                                                                                | Defines the protocol and adapter contract; AgentPresetConfig stores adapter configuration and each immutable AgentPresetRevision freezes it                |
| Accepted input and source-descriptor persistence   | [Durable Run State](14-run-persistence.md) and [Agent Control: Active Execution](35-agent-control-active-execution.md)                                      | Persists initial Run input or an accepted Thread inbox steer, including exact managed Asset IDs or external source descriptions but not source file bodies |
| Managed binary identity and content                | [Asset Management](37-asset-management.md)                                                                                                                  | Supplies immutable Workspace Asset IDs, metadata, content, lifecycle, and current authorization                                                            |
| Environment binding and path materialization       | [Environment Configuration](19-environment-management.md)                                                                                                   | Supplies authorized current state and fresh Environment adapters                                                                                           |
| Native process-local input                         | [Harness Input, Model, and Output](../agent-harness/16-input-model-and-output.md) and [Harness Public API](../agent-harness/14-public-api-and-packaging.md) | Receives native `RunInputValue` after the Foundation adapter resolves durable input                                                                        |
| Run acceptance, retry, and waiting feedback        | [Agent Control: Input and Continuation](34-agent-control-input-and-continuation.md)                                                                         | Determines when input creates a Run and when correlated feedback is a distinct protocol                                                                    |
| Steering and interrupt of already accepted work    | [Agent Control: Active Execution](35-agent-control-active-execution.md)                                                                                     | Owns Thread inbox acceptance, consumption, interruption ordering, fencing, and durable outcome                                                             |
| Idempotency evidence and unknown acceptance        | [Durable Operations and Outbox](06-durable-operations-and-outbox.md)                                                                                        | Owns request evidence and reconciliation around accepted input                                                                                             |

## Agent Input Protocol

The following definitions are the serialized wire schema. JSON arrays represent the tuple fields. Unknown fields, unknown discriminators, an unsupported `schema_version`, malformed JSON, non-finite numbers, and duplicate object keys are invalid.

```python
class AgentInput:
    schema_version: Literal["1", "2"]
    content: tuple[ContentBlock, ...] = ()
    structured_content: JsonValue | None = None


type ContentBlock = TextContent | BinaryContent


class TextContent:
    type: Literal["text"]
    text: str


class BinaryContent:
    type: Literal["binary"]
    source: BinaryContentSource
    filename: str | None = None
    media_type: str | None = None
    delivery: BinaryContentDelivery = "auto"


type BinaryContentSource = UrlBinarySource | PathBinarySource | AssetBinarySource


class UrlBinarySource:
    type: Literal["url"]
    url: str


class PathBinarySource:
    type: Literal["path"]
    environment_binding: str
    path: str


class AssetBinarySource:
    type: Literal["asset"]
    asset_id: AssetId


type BinaryContentDelivery = Literal[
    "auto",
    "model_content",
    "model_url",
    "environment_path",
]
```

Schema version `1` retains the previous `url | path` source union. Schema version `2` uses the complete union shown above and is the default for new SDK submissions. Readers and Workers continue to interpret retained version `1` values under their original contract; an `asset` source is invalid unless `schema_version="2"`.

The same version-appropriate source union is used for submission and the accepted value. Foundation does not accept inline binary bodies in `AgentInput`. A caller that owns local bytes can first publish them as an immutable [Asset](37-asset-management.md#native-management-api), publish them through an authorized Environment path, or use an eligible URL.

`content` is an ordered sequence representing one user-authored message boundary; it never accepts system, assistant, or tool roles. A `TextContent.text` value is non-empty and retains its exact Unicode value and position. `structured_content` is the independent Agent-specific machine-readable channel. It is not a JSON content block, cannot carry control or authority fields, and enters model context only through the selected AgentPresetRevision's locked input adapter.

Text, structured content, and binary content are independently optional and can appear in any combination, including an empty input. AgentPresetRevisions do not enable or disable `AgentInput` block types, media types, sources, or deliveries. Foundation-wide hard limits and content policy still apply.

`filename` is bounded display metadata, never an object key or Environment path. Every byte sequence remains `BinaryContent`; media type and delivery determine later handling.

For `source.type="asset"`, omitted `filename` and `media_type` resolve from the Asset's immutable metadata when the Worker constructs delivery. A supplied value must equal that normalized Asset metadata at acceptance; it is not an Asset rename or reinterpretation. The accepted JSON can therefore contain only `asset_id` plus the chosen delivery when the caller uses Asset metadata unchanged.

`media_type` is an extensible MIME media-type essence, not a closed enum or a snapshot of the IANA registry. A submitted non-null value must match the MIME `type/subtype` token grammar and contain no parameters or wildcards. Foundation lowercases it during acceptance. Registry membership can inform policy and observability, but absence from the current registry is not by itself invalid; vendor, personal, and mutually agreed private types remain representable.

Foundation can validate that syntax deterministically, but it cannot prove every media type from bytes alone. When the Worker reads bytes for `model_content` or `environment_path`, it applies bounded signature or format detection when a reliable detector exists and rejects a positive conflict with the submitted or Asset metadata hint. An unidentified value is treated as `application/octet-stream` for that execution. Detection is process-local and does not rewrite the accepted input or Asset. The direct `url` plus `model_url` case requires an explicit syntactically valid `media_type` because Foundation never reads its bytes; the Model provider owns byte/type agreement. Content policy, the frozen Model snapshot, selected delivery, and deployment policy can each narrow the usable set.

## Binary Source and Delivery

`source` and `delivery` are independent axes:

| Axis       | Question answered                                  | Values                                                                        |
| ---------- | -------------------------------------------------- | ----------------------------------------------------------------------------- |
| `source`   | Where can execution obtain the content?            | Caller URL, authorized Sandbox/Environment path, or immutable Workspace Asset |
| `delivery` | How does the Worker expose accepted content later? | Native model content, model URL, or a default-Environment file path           |

Source forms have these contracts:

| Source  | Contract                                                                                                                                                                                                                                                                                                                                                               |
| ------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `url`   | A credential-free absolute HTTP(S) URL with no caller headers. Acceptance validates syntax and permitted destination policy. A Worker fetch additionally bounds redirects, resolved destinations, time, and response size. Authenticated content uses its Connector or another authorized ingress. `model_url` is the only mode in which Foundation does not fetch it. |
| `path`  | A normalized relative POSIX path under an authorized Sandbox or Environment binding, never a Worker host path. Acceptance validates binding authority and rejects absolute paths or traversal. A Worker read additionally rejects symlink escape, directories, unsupported file kinds, inaccessible bindings, and oversize content.                                    |
| `asset` | An exact immutable `asset_id` owned by the same Workspace. Acceptance authorizes `asset.use` and active lifecycle without copying bytes. A Worker reauthorizes the active Asset and verifies its selected object and digest before delivery. The ID never resolves to replacement content.                                                                             |

Acquisition and delivery are separate phases. Acceptance retains the normalized URL or path description or the exact `asset_id` and resolves `auto` without reading the file body. A Worker later reads the source only when the selected delivery needs bytes. URL and path sources create no managed copy, digest reference, or retention timer. Asset content and retention remain owned by the independent Asset resource.

Delivery forms have these contracts:

| Delivery           | Worker behavior                                                                                                                                                                                                                                                                                                                                                                    |
| ------------------ | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `model_content`    | Reads the accepted `url`, `path`, or `asset` into bounded Worker-owned memory or private staging, detects and validates its media type when possible, constructs native Pydantic `BinaryContent`, and adds it to Harness `RunInputValue`. It never writes the content into an Environment. Provider encoding, upload, or transport remains with the selected native Model adapter. |
| `model_url`        | Requires `source.type="url"` and passes that exact caller URL directly to the matching native Model URL value. Foundation neither downloads it nor mints another URL, and it never writes an Environment file.                                                                                                                                                                     |
| `environment_path` | Requires a writable default Environment binding. The Worker reads the accepted `url`, authorized source `path`, or exact `asset` into a bounded Worker-local staging file, then writes it through the active default Environment mount's authorized streaming file-write operation. Only the resulting logical path enters Harness `RunInputValue`.                                |

The canonical `environment_path` is `/workspace/.a13n/inputs/{input_instance_id}/content-{block_index}`, where `input_instance_id` is the already existing owning Run ID for Run input or Thread-inbox entry ID for steer, and `block_index` is the binary block's zero-based position in `AgentInput.content`. `/workspace/.a13n/inputs` is reserved for Foundation input materialization. The Worker creates missing parent directories, replaces the deterministic target with one complete file, and removes its private staging file after the transfer finishes. A Worker host path never enters Agent input. The accepted `filename` and `media_type` remain separate metadata and do not influence the sandbox path.

`auto` is submission-only. Acceptance resolves it using the source form, submitted media type, frozen Model execution snapshot, selected Environment configuration, and current policy; an explicit delivery must satisfy the same constraints. Byte-dependent limits are enforced when a Worker reads the source. Every replacement RunAttempt reuses the frozen mode.

`model_content` and `model_url` stop after constructing model-native input; they never materialize a sandbox file. `environment_path` selects no temporary or persistent retention class. The materialized file is non-authoritative execution data whose lifetime follows the backing target represented by current Host state. Materialization adds no file-specific lifecycle management. Harness close is non-destructive, while retention, explicit destroy, cleanup, and prune remain owned by the [Environment contract](19-environment-management.md#warmup-cleanup-and-prune).

For initial Run input, a replacement Attempt derives materialization behavior from existing durable model-request usage. If the Run's charged prior-attempt `model_requests` total is zero, it reads the source and replaces every `environment_path` target again before Harness execution. If the total is positive, it assumes the deterministic target was already written and does not read or rewrite it. This is a runtime decision, not a stored input flag. A pending steer is handled independently after it binds to a running Run: its source is read and its target is materialized until the existing inbox receipt makes that steer `consumed`.

Foundation does not guarantee that a URL or Environment path yields identical bytes across acceptance, Worker replacement, or explicit Retry. Each read revalidates current source authority, safety, type, and bounds and fails closed if the source is unavailable or no longer valid. An Asset ID does select identical immutable bytes across those reads while the Asset remains active; deletion or content unavailability fails closed and never rebinds the ID.

## Accepted Input and Canonicalization

Before durable Run or steer acceptance, Foundation resolves `delivery="auto"`, normalizes the source description and any submitted media type, and freezes one concrete delivery. `model_url` additionally requires a URL source and non-null media type. An Asset source additionally requires an active same-Workspace Asset and current `asset.use` authority. Acceptance does not fetch source bytes or infer a media type from them.

Foundation canonicalizes the complete accepted value as UTF-8 RFC 8785 JSON. It stores that JSON directly in the owning Run or inbox row, or in an immutable [Run payload object](14-run-persistence.md#run-payload-object) when it exceeds the inline JSON bound. Each binary block contains only its normalized URL or Environment-path description or exact immutable `asset_id`, never inline bytes, an object-storage key, or a copied Asset body. The canonical JSON supplies durable accepted-descriptor integrity. Idempotency evidence and final acceptance follow [Durable Operations and Outbox](06-durable-operations-and-outbox.md).

## Input Adapter and Harness Mapping

Every `AgentPresetConfig` stores one trusted input adapter key and bounded configuration. Revision creation validates them against the selected Plugin Runtime profile and freezes them in the immutable AgentPresetRevision. The Revision does not carry an input-type declaration or per-input limits.

[Agent Management](12-agent-management.md#protocol-configuration) can define an optional `ProtocolConfig.input_data_schema` for non-null `structured_content`. Absent structured content remains valid, and the schema does not restrict text or binary blocks, media types, sources, or deliveries. Acceptance validates the wire, Foundation hard limits, source authority, content policy, delivery feasibility, and any applicable structured-content schema.

The immutable AgentPresetRevision freezes the trusted adapter key and configuration; the Run pins that Revision and one compatible Runtime lock. After verifying both, the Worker preserves block order, maps `TextContent` to native user text, applies the delivery table above to binary content, and lets only the locked adapter incorporate `structured_content` into Harness `RunInputValue`.

The adapter returns `None` only for accepted empty input. It receives no credential or ambient authority and cannot widen frozen execution or content policy. Replacement Workers use the same accepted input, Revision, and Runtime lock; an unavailable or incompatible adapter fails before model or tool work.

## Use by Control Operation

| Operation             | Behavior                                                                                                                                                 |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Root invocation       | Stores new accepted input on the root Run.                                                                                                               |
| Continuation          | Stores new accepted input on a successor accepted by Continue, Continue From, or queue consumption.                                                      |
| Thread Run submission | Accepts input immediately when the Thread is eligible; otherwise stores the complete editable Run intent until consumption accepts a Run.                |
| Fork                  | Stores new accepted input on the new Thread's first Run.                                                                                                 |
| Managed Trigger       | Places bounded machine data in `structured_content`, validating its optional protocol schema.                                                            |
| Asynchronous child    | Stores parent- or Host-supplied input on the child Run.                                                                                                  |
| Async child result    | Uses the separate Host-owned inbox payload and active-or-successor delivery contract; it is not `AgentInput`.                                            |
| Steer                 | Stores accepted input in the Thread inbox without creating a Run; it binds to the current running Run or records the current/head waiting Run as source. |
| Retry                 | Copies the source Run's accepted input, preserving the same Asset ID or external source description, and reacquires any needed bytes for the new Run.    |
| Waiting feedback      | Uses the separate [atomic feedback protocol](34-agent-control-input-and-continuation.md#deferred-interaction).                                           |
| Waiting Continue      | Stores the input inside one composite `WaitingRunContinueInput`; the same first model request receives it and the default deferred results.              |

## Failure Semantics

| Condition                                                                | Outcome                                                                                               |
| ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- |
| Invalid schema, MIME syntax, source description, or delivery             | Request is rejected before Run or steer acceptance                                                    |
| Source binding or URL destination is unauthorized                        | Request is rejected without exposing private existence                                                |
| Asset is absent, deleted, cross-Workspace, or unauthorized at acceptance | Request is rejected without copying or exposing Asset content                                         |
| Accepted Asset is deleted or unavailable before delivery                 | Execution fails before bytes cross the Model or Environment boundary                                  |
| Runtime source is absent, unsafe, inaccessible, or oversize              | Execution fails before its model or Environment boundary                                              |
| Detected bytes conflict with the submitted media-type hint               | Execution fails before the bytes are supplied to a model or Environment                               |
| Direct model URL cannot be fetched or has incompatible content           | The Model request fails through the selected provider's ordinary error mapping                        |
| Environment staging or file writing fails                                | Execution fails before the model request; no Worker host path or partial Environment path is supplied |
| Locked adapter is absent, incompatible, or rejects mapping               | Execution fails before model or tool work; Worker never substitutes another adapter                   |

## Compatibility

`AgentInput.schema_version` versions the complete wire and accepted-value contract; public type names have no version suffix. Version `2` adds `AssetBinarySource` without reinterpreting retained version `1` input. Accepted values retain their version, the pinned Runtime and adapter must support it, and Workers never upgrade stored input. A new block, discriminator, delivery, required field, canonicalization rule, or changed field meaning requires another version. Unknown versions and discriminators fail closed. SDK conveniences serialize the same protocol and default new submissions to version `2`.

## Invariants

1. `AgentInput` is the single ordinary caller- or Host-submitted semantic-input protocol for root invocation, continuation, queued submission, fork, active steering, managed Trigger input, and input sent to an asynchronous child; a returning child result retains its separate typed Agent provenance.
2. Accepted binary input persists only a normalized caller URL, authorized Environment-path description, or exact immutable `asset_id`; it never persists inline bytes or an object-storage key.
3. `structured_content` is bounded JSON, follows the optional frozen protocol schema when non-null, carries no authority, and never becomes implicit model JSON.
4. Harness mapping uses the pinned Revision and Runtime lock and fails closed rather than substituting input representation.
5. Only `environment_path` writes binary input into an Environment, always below the reserved default-workspace input root and only through the Environment file interface.
6. Same-Run Environment-file rematerialization is derived from existing model-request usage, while pending steer rematerialization is derived from existing inbox consumption evidence; neither adds a materialization field.
7. An Asset ID is an exact immutable content selection while active; Run acceptance stores no Asset revision, body snapshot, or Asset-specific Run state.
