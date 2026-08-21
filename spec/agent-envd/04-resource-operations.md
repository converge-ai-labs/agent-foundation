# Resource Operations

## Design Position

`agent-envd` exposes semantic, mount-scoped filesystem operations and bounded observation of local listening ports. It resolves native paths and races inside the Environment boundary rather than exposing host absolute paths or asking the EIP client to emulate filesystem behavior through low-level syscalls.

Trusted daemon configuration defines every native mount root and its maximum access. EIP requests select logical mount IDs and relative paths within those roots; they cannot add a host path, remount a resource, or widen read-only policy.

## Boundaries

| Concern                                                                             | Owner                                                    | Relationship                                         |
| ----------------------------------------------------------------------------------- | -------------------------------------------------------- | ---------------------------------------------------- |
| Native mount roots, writable ceilings, protected roots, and port-observation policy | Operator or provider adapter                             | Trusted immutable daemon configuration               |
| Harness virtual `/workspace` and `/environment/{alias}` routing                     | Harness                                                  | Resolves to one binding before EIP dispatch          |
| Logical mount paths and file, search, and port methods                              | This document                                            | Stable EIP resource contract                         |
| Native path canonicalization, symlink containment, compare-and-swap, and receipts   | `agent-envd`                                             | Authoritative provider enforcement                   |
| Provider ingress, public URL, tunnel, or container port publishing                  | Provider adapter                                         | Outside EIP port observation                         |
| List, search, and retained-result output                                            | [Output Retention](06-output-retention.md)               | Applies to JSON results, not raw file-transfer bytes |
| Raw file-transfer framing, attachment, and backpressure                             | [Transports and Sessions](03-transports-and-sessions.md) | Carries bytes for the transfer lifecycle owned here  |

Filesystem selectors and port numbers are not authority. Every method also crosses authentication, capability checks, current generation, and configured ceilings.

## Mount Model

Trusted daemon configuration uses this conceptual, non-EIP shape:

```python
class TrustedMountConfig(BaseModel):
    mount_id: str
    native_root: str
    staging_root: str | None = None
    writable: bool
    allow_command_execution: bool = True
    max_file_bytes: int
    allowed_operations: frozenset[str]
```

`native_root` and `staging_root` are operator-only secret-adjacent configuration: envd canonicalizes and identity-checks them but never returns them in EIP. `allowed_operations` is a subset of the file-read, file-write, search, command-cwd, and executable-source families supported by the daemon. `writable=false` is an absolute ceiling for file methods and required-isolation command grants.

A mount that permits any staged file mutation or binary writer requires one private `staging_root` on the same native filesystem as every writable destination covered by that mount. The staging root is outside every logical EIP mount, command filesystem grant, execution home, temporary root, retained-output root, and other caller-reachable namespace. Envd owns it with restrictive permissions and opens candidates relative to a pinned root identity. Its layout is flat: only bounded-name regular candidate files and fixed-size envd ownership metadata are valid; symlinks, nested directories, hard-link surprises, and unknown entries fail startup rather than triggering recursive traversal. A writable mount cannot cross into another native filesystem; such a destination returns `unsupported` before staging. If configured policy allows staged writes but same-filesystem atomic rename, root privacy, or payload exclusion cannot be enforced, startup fails for that configuration. A mount can deliberately omit staged-write operations and advertise no atomic replacement support, but envd never falls back to a visible temporary file or direct write. In explicit `disabled` isolation, the outer provider must hide and protect the staging root from payloads even when payload and daemon native identities would otherwise overlap.

The following objects are serialized EIP JSON:

```python
class EIPPath(BaseModel):
    mount_id: str
    path: str


class MountDescriptor(BaseModel):
    mount_id: str
    logical_root: str
    writable: bool
    case_sensitive: bool | None
    supports_atomic_replace: bool
    supports_file_revision: bool
    max_file_bytes: int


class FileRevision(BaseModel):
    value: str
```

`mount_id` is a bounded stable logical ID within one Environment generation. `logical_root` is a display-only EIP path such as `/`; it is not a host path. `path` is absolute within that logical mount, begins with `/`, uses `/` separators on the wire, contains no NUL, and is validated lexically before native resolution. `/` is the mount root; otherwise empty interior segments, a trailing separator, `.`, and `..` are rejected instead of normalized into another request.

A trusted mount configuration binds the logical ID to one canonical native directory, read-only or read-write ceiling, protected-path checks, allowed operation families, file-size bounds, and platform semantics. `supports_atomic_replace=true` requires the configured private staging boundary, same-filesystem destination coverage, candidate revalidation, and atomic rename contract above; a native rename primitive alone is insufficient. The descriptor reports observed behavior but cannot widen the configuration.

Native mount roots are non-overlapping after canonicalization unless they are exact aliases with identical policy intentionally represented as one mount. Ancestor/descendant roots with competing policies are rejected at startup. This avoids backend-specific precedence and accidental widening.

## Canonicalization and Symlinks

For every operation, envd:

1. selects the trusted mount by exact `mount_id`;
2. validates the lexical wire path and operation-specific size bounds;
3. resolves components relative to an already opened or identity-checked mount root;
4. checks every traversed symlink target remains inside the same authorized root and outside protected paths;
5. applies read/write and operation policy to the resolved resource;
6. uses descriptor-relative, no-follow, file-identity, or equivalent race-resistant native operations where available;
7. revalidates mutation preconditions at the commit boundary.

A symlink can narrow convenience but cannot expand authority. A link inside a writable workspace that targets daemon state, another mount, the host home, or any unexposed path remains inaccessible. The daemon does not return a native canonical path to the client.

`file.stat` can inspect the link itself or a contained target through an explicit `follow_symlinks` flag. Read, list, search, copy-source, and patch operations follow contained symlinks by default. Write replacement does not replace an out-of-root target. `file.remove` removes the selected directory entry itself and never recursively follows a symlink. Recursive traversal tracks visited directory identities and has finite depth and entry ceilings.

Canonicalization failure is pre-dispatch for the requested filesystem mutation. A root or component identity changed during setup returns a conflict or denial; envd never retries against the replacement path silently.

## Common File Types

```python
type FileKind = Literal[
    "file", "directory", "symlink", "other"
]


class FileInfo(BaseModel):
    path: EIPPath
    kind: FileKind
    size_bytes: int | None
    modified_at: datetime | None
    executable: bool | None
    revision: FileRevision | None
```

`FileRevision` is an opaque provider comparison value scoped to Environment generation and resource identity. It is not content, authority, or guaranteed stable across replacement. When a provider cannot produce a safe revision, the field is `null` and compare-and-swap requiring it is unsupported.

Timestamps and executable bits are observations with platform-specific precision. EIP does not expose owner IDs, ACL internals, native inode numbers, extended attributes, device nodes, sockets, or arbitrary platform metadata through the base file contract.

## Read Operations

### `file.stat`

```python
class FileStatParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    follow_symlinks: bool = True


class FileStatResult(BaseModel):
    info: FileInfo
```

A missing path returns `not_found_or_denied`. Following a symlink that leaves the mount returns `denied` without disclosing the target.

### `file.read_text`

`file.read_text` is the bounded UTF-8 convenience operation used by editors, Agent file tools, and other callers that need text semantics rather than a raw download.

```python
class FileTextCursor(RootModel[str]): ...


class TextPosition(BaseModel):
    line: int
    byte_column: int


class FileReadTextParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    cursor: FileTextCursor | None = None
    start_line: int | None = None
    max_lines: int | None = None
    max_bytes: int | None = None
    expected_revision: FileRevision | None = None


class FileReadTextResult(BaseModel):
    info: FileInfo
    text: str
    start: TextPosition
    end: TextPosition
    next_cursor: FileTextCursor | None
    content_complete: bool
    truncated: bool
```

At most one of `cursor` or `start_line` is present; omitting both starts at line one. Lines are one-based; `byte_column` is zero-based within the UTF-8 bytes of that line. The daemon decodes strictly as UTF-8, preserves line endings and every valid scalar exactly, and never performs lossy replacement, newline normalization, MIME inference, or binary-to-text coercion. Invalid UTF-8 or NUL in the bytes needed for this bounded page returns `unsupported` without a partial value for that call. Page validation reads only the selected segment plus bounded boundary lookahead; it does not scan the remainder of the file, so an earlier successful page is not proof that every later byte is valid text. A later invalid segment fails the call that reaches it.

The effective line and byte limits are finite and reserve JSON-envelope overhead. A result never splits a UTF-8 scalar, but a byte ceiling can end within a very long logical line; `end`, the opaque continuation cursor, and the next result preserve that position exactly. `truncated=true` means the requested convenience page stopped at an effective limit. `content_complete=true` means this bounded observation reached EOF from its selected start position. The cursor binds the authenticated user, generation, canonical file identity, safe revision, and next text position. Envd returns a continuation cursor only when the mount can preserve that revision comparison; replacement or revision change invalidates it with `conflict`. Without safe revision support, one call still returns a correct bounded observation, but an incomplete result has no cursor and never claims that a later call can continue the same version. The caller uses a raw reader for exact complete bytes or starts another explicitly separate text observation.

`max_bytes` counts decoded UTF-8 source bytes before JSON escaping. The response limit can narrow it further. `file.read_text` is intentionally unsuitable for arbitrary binary content or an unbounded complete file; those use a binary reader.

### Binary reader

Raw file content, including a text file being downloaded rather than edited, uses one session-scoped binary reader and the transport's raw data plane.

```python
class FileReaderHandle(RootModel[str]): ...


class ContentDigest(BaseModel):
    algorithm: Literal["sha256"]
    value: str  # exactly 64 lowercase hexadecimal characters


class FileByteRange(BaseModel):
    offset: int = 0
    length: int | None = None


class FileReaderOpenParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    byte_range: FileByteRange | None = None
    expected_revision: FileRevision | None = None
    transfer_deadline: datetime | None = None


class FileReaderOpenResult(BaseModel):
    reader: FileReaderHandle
    info: FileInfo
    range_start: int
    range_end: int
    source_eof_at_end: bool
    expires_at: datetime


class FileReadCompletion(BaseModel):
    range_start: int
    range_end: int
    produced_bytes: int
    digest: ContentDigest | None
    source_eof_at_end: bool
    stability: Literal["verified", "changed", "unverified"]
    complete: bool


class FileReaderCloseParams(BaseModel):
    context: EIPCallContext
    reader: FileReaderHandle
    accept_complete: bool


class FileReaderCloseResult(BaseModel):
    completion: FileReadCompletion
```

`file.open_reader` authorizes and opens one existing regular file, pins its native identity, validates `expected_revision` when supplied, computes the selected half-open interval from the size observed at open, and returns before any raw byte is accepted as successfully transferred. A missing range selects `[0, observed_size)`. `offset` and `length` count bytes; the range is clipped at the observed EOF, while an offset beyond it is invalid. The selected end remains fixed even if the file later grows. Native EOF before that end is a source reset and incomplete close, not successful terminal EOF. A range is an open-time selector for HTTP Range, random access, or explicit resume. It is not the ordinary full-file continuation mechanism. The selected interval must fit the effective finite per-transfer and mount read ceiling; a larger full-file request fails before attachment rather than silently becoming partial.

The call context deadline bounds opening only. `expires_at` is the minimum of a valid requested `transfer_deadline` and the daemon's absolute transfer-duration ceiling; omission selects that finite daemon ceiling. Valid progress refreshes the shorter idle timer but never extends `expires_at`.

After the successful open response, the selected transport attaches exactly one server-to-client data stream to `reader`. Bytes are emitted sequentially from transfer offset zero, corresponding to native `range_start`, under transport backpressure and are never base64-encoded or copied into a JSON result. Each framed carrier verifies the exact next offset; the terminal data marker or exact HTTP response-body EOF means the producer has no more bytes for that attachment. For stdio and WebSocket, the client sends `END_ACK` only after the high-level consumer has drained every queued chunk and observed `END`; bounded transport prefetch does not acknowledge application consumption. For HTTP, `file.close_reader(accept_complete=true)` after reading the exact declared body is the equivalent consumer acknowledgement.

The first `file.close_reader` call linearizes the caller's decision. `accept_complete=true` succeeds as complete only after clean producer termination and consumer acknowledgement for the complete selected interval. `accept_complete=false` cancels or discards any remaining attachment and finalizes an incomplete read even if the producer had already emitted more bytes. Repeating the same close replays its bounded result while the session record remains; a conflicting later choice returns `conflict`. A high-level reader hides attachment and close, keeps its own byte count and SHA-256 while yielding chunks, calls `accept_complete=true` only after iteration reaches the terminal boundary, and compares its local evidence with envd. Leaving the context early calls or effects `accept_complete=false` and never reports normal completion.

`produced_bytes` is the number of source bytes envd read and handed to the local carrier before termination; on an incomplete read it is not proof that the remote consumer accepted that prefix. Envd computes SHA-256 incrementally, but returns a non-null `digest` only when `complete=true`, in which case `produced_bytes == range_end - range_start`, terminal acknowledgement succeeded, and the client's local count and digest must match. An incomplete result has `digest=null` rather than mislabeling queued or partially delivered bytes as peer-verified evidence.

At terminal close envd re-observes a safe file revision when the mount supports one. `stability="verified"` means the revision at close equals the revision observed at open; `changed` preserves complete byte-integrity evidence but causes a high-level exact reader to raise `conflict` because already delivered bytes cannot be retracted; `unverified` makes no immutable-snapshot claim. Opening a file descriptor pins resource identity but does not fabricate snapshot isolation against external in-place writes. A consumer that requires publish-once consistency spools the bounded transfer until successful completion, or requires a mount with safe revision support.

A reader that has reached producer termination but has not linearized its first close remains `awaiting_close`: it continues to count against active transfer and record limits, is not capacity-reclaimable, and expires only under its session or finite transfer deadlines. Only the cached post-close result or tombstone is terminal and eligible for TTL or oldest-terminal-first reclamation. A reader cannot be attached from another session, resumed after reconnect, or converted into an output reference. Reopening with an explicit range and expected revision is the resume mechanism.

### `file.list`

```python
class FileListParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    recursive: bool = False
    max_depth: int = 1
    cursor: OutputCursor | None = None
    output_policy: OutputPolicy | None = None


class FileListEntry(BaseModel):
    relative_path: str
    info: FileInfo


class FileListResult(BaseModel):
    entries: tuple[FileListEntry, ...]
    output: StructuredOutputDisposition
```

Entries are returned in a documented stable bytewise path order for one captured request shape. A cursor binds mount, canonical root, recursive flag, depth, generation, ordering, and authorization. Directory mutation between pages can produce a typed invalidated cursor or a provider snapshot when advertised; EIP never presents an unstable traversal as a complete snapshot.

Directory entry names are bounded. A native name that cannot be represented safely in EIP UTF-8 causes an explicit `unsupported` failure before the affected page is returned; it is never silently lossy-decoded, skipped, or represented as another path.

### `file.find`

`file.find` searches path names only. It never opens regular-file content.

```python
class FileFindParams(BaseModel):
    context: EIPCallContext
    root: EIPPath
    pattern: str
    mode: Literal["glob", "regex"]
    kind: FileKind | None = None
    max_depth: int
    cursor: OutputCursor | None = None
    output_policy: OutputPolicy | None = None


class FileFindResult(BaseModel):
    entries: tuple[FileListEntry, ...]
    output: StructuredOutputDisposition
```

The pattern matches each descendant's `/`-separated path relative to `root`, never a native path; `root` itself is not a result. Glob syntax is the EIP subset `*`, `?`, `**`, and bracket character classes: `*`, `?`, and classes never match `/`, while `**` matches zero or more complete path segments. Regex mode uses UTF-8 RE2-style syntax without look-around or backreferences and matches the complete relative path. Results use the same stable bytewise relative-path order and cursor invalidation rules as `file.list`.

### `file.search`

`file.search` searches regular-file content. Path selection is separate from content matching: `include` and `exclude` are EIP path globs relative to `root`, while `query` is matched only against decoded file content.

```python
class FileSearchParams(BaseModel):
    context: EIPCallContext
    root: EIPPath
    query: str
    mode: Literal["literal", "regex"]
    include: tuple[str, ...] = ()
    exclude: tuple[str, ...] = ()
    case_sensitive: bool = True
    max_depth: int
    cursor: OutputCursor | None = None
    output_policy: OutputPolicy | None = None


class FileSearchMatch(BaseModel):
    path: EIPPath
    line_number: int
    byte_offset: int
    preview: str


class FileSearchResult(BaseModel):
    matches: tuple[FileSearchMatch, ...]
    output: StructuredOutputDisposition
```

Search reads UTF-8 regular files line by line. A file containing invalid UTF-8 or NUL is skipped deterministically and never lossily decoded. `include=()` selects every relative path; otherwise a file must match at least one include glob, and any matching exclude glob wins. Both use the `file.find` glob dialect.

`line_number` is one-based, `byte_offset` is the zero-based raw-file offset of the match, and `preview` is one bounded containing line with line terminators removed. Results are ordered by relative path, then byte offset. Literal mode emits every non-overlapping occurrence of the exact query text. Regex mode uses the same RE2-style syntax as `file.find`, is applied independently to each line, and cannot span line boundaries; a zero-width match advances by one Unicode scalar before another match is considered. `case_sensitive=false` uses Unicode simple case folding and is explicit rather than inferred from the filesystem.

Query length, regex complexity, glob count, traversal depth, files visited, bytes scanned, match count, preview bytes, and total duration are finite. Find and search implementations use bounded streaming and cancellation. A policy cutoff returns an incomplete `StructuredOutputDisposition` and continuation cursor when the traversal can continue safely; a deadline or cancellation uses the typed EIP error and can include only bounded partial-output metadata. Absence of further results is claimed only when traversal completed.

Neither method follows a symlink outside the selected mount or reads special files. Content previews are untrusted file content and follow the Harness content and redaction policy after provider-side size enforcement. `file.find` and `file.search` have independent capabilities; omitting either capability is valid, but advertising one requires its complete semantics.

## Mutation Operations

All mutations return an `OperationReceipt` and the resulting resource revision when known. A method validates its entire semantic request before native commit. Idempotency support is method- and mode-specific and follows [EIP Protocol](02-eip-protocol.md#common-operation-context).

### `file.write_text`

`file.write_text` is the bounded UTF-8 convenience mutation. It uses JSON text only when the complete bounded value is already appropriate for an editor or Agent operation.

```python
type FileWriteMode = Literal[
    "create", "replace", "upsert", "append"
]


class FileWriteTextParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    mode: FileWriteMode
    text: str
    expected_revision: FileRevision | None = None
    executable: bool | None = None


class FileWriteTextResult(BaseModel):
    info: FileInfo
    bytes_written: int
    receipt: OperationReceipt
```

The exact UTF-8 encoding of `text` is bounded by request, response-independent file, and mount limits before staging. Envd rejects NUL and performs no newline, Unicode-normalization, encoding, or BOM transformation. `create` requires absence, `replace` requires an existing regular file, `upsert` allows either, and `append` produces a replacement containing the prior bytes followed by the supplied bytes. `append` requires an existing strict UTF-8, NUL-free file, safe revision support, an explicit matching `expected_revision`, and an idempotency key for replay-safe mutation. The parent directory must already exist; callers use the separately receipted `file.mkdir` operation rather than hiding non-atomic directory creation inside a write.

Every mode stages a complete candidate under the selected mount's pinned private staging root and applies the expected-revision and destination preconditions again at commit. The candidate is never addressable through EIP or command grants. Envd records the digest of the intended complete bytes while building the candidate, then identity-checks and rehashes the complete sealed candidate against that digest immediately before one same-filesystem atomic directory-entry commit; unsupported privacy, integrity revalidation, or atomic replacement fails before target mutation. Validation failure or a cancellation proven before commit removes the candidate and leaves the destination unchanged. Transport loss after method acceptance does not itself cancel this single-request mutation; the client reconciles its operation ID, idempotency key, receipt, or resource revision before retrying.

### Binary writer

A raw upload uses one session-scoped binary writer. The destination is not changed by open, data attachment, or data-stream completion.

```python
class FileWriterHandle(RootModel[str]): ...


class FileWriterOpenParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    mode: FileWriteMode
    expected_revision: FileRevision | None = None
    executable: bool | None = None
    transfer_deadline: datetime | None = None


class FileWriterOpenResult(BaseModel):
    writer: FileWriterHandle
    max_transfer_bytes: int
    expires_at: datetime


class FileWriterCommitParams(BaseModel):
    context: EIPCallContext
    writer: FileWriterHandle
    transferred_bytes: int
    transfer_digest: ContentDigest


class FileWriterCommitResult(BaseModel):
    info: FileInfo
    transferred_bytes: int
    transfer_digest: ContentDigest
    receipt: OperationReceipt


class FileWriterAbortParams(BaseModel):
    context: EIPCallContext
    writer: FileWriterHandle


class FileWriterAbortResult(BaseModel):
    status: Literal[
        "aborted",
        "already_aborted",
        "commit_in_progress",
        "already_committed",
    ]
```

`file.open_writer` authorizes the destination, reserves one writer and staging-object record, creates a candidate beneath the mount's pinned private same-filesystem staging root, and captures destination preconditions. The parent directory must already exist. For append, safe revision support and an explicit `expected_revision` are required; envd copies that authorized existing revision into the candidate before accepting uploaded bytes, and the copied prefix consumes staging quota. The candidate is opened by pinned identity with restrictive permissions and never becomes caller-addressable. It returns only after failure can still be rolled back without changing the destination. `max_transfer_bytes` is the current individual hard ceiling, including the remaining final-file allowance for append; aggregate staging capacity is still reserved incrementally and is not promised by that value. The call context deadline bounds opening only, while `transfer_deadline`, idle expiry, and `expires_at` follow the same finite rules as a reader.

The transport then attaches exactly one client-to-server data stream. Payload offsets begin at zero and cover only bytes supplied by this transfer, including append bytes but not the copied prefix. Envd enforces exact contiguous offsets, frame and total limits, incremental staging quota, idle and absolute deadlines, and SHA-256 while writing bounded chunks. A terminal data marker or HTTP request-body EOF closes the data attachment but does not mutate the target.

`file.commit_writer` is eligible only after a clean terminal marker and data-plane acknowledgement has sealed the writer. Its domain admission is one coordinator linearization: while the session still admits work, envd validates the sealed writer and exact requested byte count/`transfer_digest` against attachment observations, reserves the generation-scoped operation record, and transfers candidate ownership from the session to that operation atomically. A request mismatch transitions the session-owned writer to cleanup and returns `integrity_mismatch` without handoff or target mutation. If session closing wins first, commit fails pre-dispatch and session cleanup owns deletion; if handoff wins first, every session cleanup path ignores the candidate and the operation proceeds independently.

After handoff, envd revalidates the pinned candidate identity, complete size and metadata, and hashes the complete sealed candidate immediately before rename. For create, replace, and upsert that full hash must equal `transfer_digest`; for append envd revalidates the copied source revision and prefix and hashes the staged suffix range against `transfer_digest`. Any candidate mismatch returns `integrity_mismatch`, deletes the candidate, and performs no target mutation. Envd then revalidates destination path identity, mode, revision, size, metadata, and policy; applies configured file and containing-directory durability; and performs one atomic commit. After ownership handoff the transfer deadline no longer owns cleanup; the commit call context and daemon operation-duration ceiling bound the accepted mutation. The result and receipt identify that mutation completion boundary. Atomic visibility and configured `fsync` behavior do not imply Host durable Agent completion or a stronger universal power-loss guarantee than the selected filesystem and provider actually supply.

`file.abort_writer`, early context exit, data-carrier failure, transfer expiry, session loss, and daemon drain delete the candidate and leave the destination unchanged only while the writer remains open, receiving, or sealed. Once commit owns the candidate, abort reports `commit_in_progress` or `already_committed` and cannot promise rollback; session cleanup never races deletion against that commit. Cancellation and connection loss during commit preserve the ordinary completed/cancelled/unknown distinction. Staged bytes and object quota are returned exactly once only after commit removes ownership or deletion is confirmed. A failed unlink or uncertain cleanup remains conservatively charged, is not silently orphaned, and puts the mount or daemon into a cleanup-fault/draining state for operator repair.

Commit accepts an idempotency key and retains generation-scoped result and receipt evidence. If the response is lost after possible commit, a fresh authenticated session reconciles by commit `operation_id`, receipt, or retained idempotency record before opening another writer. The old writer itself never becomes valid in the new session. A matching completed idempotency record can replay the prior result without re-authorizing that expired writer for another native commit; an absent record cannot.

For create, replace, and upsert, `transfer_digest` also identifies the complete resulting file content; for append it identifies only the newly supplied suffix, while envd owns verification of the revision-checked staged prefix.

A high-level writer hides the handle, attachment handshake, offsets, terminal marker and acknowledgement, digest, and abort path. `commit()` seals the data attachment, sends a fresh operation ID and idempotency key, verifies the local count and digest against envd, and returns the committed revision and receipt. Exiting without successful commit aborts.

### `file.mkdir`

```python
class FileMkdirParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    parents: bool = False
    exist_ok: bool = False


class FileMkdirResult(BaseModel):
    info: FileInfo
    created_directories: int
    receipt: OperationReceipt
```

Creates one directory or a bounded parent chain under the selected mount. Existing-path behavior is explicit through `exist_ok`. It never treats a symlink as a directory for creation.

### `file.patch_text`

```python
class FilePatchTextParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    patch_format: Literal["unified_diff"]
    patch: str
    expected_revision: FileRevision


class FilePatchTextResult(BaseModel):
    info: FileInfo
    hunks_applied: int
    receipt: OperationReceipt
```

The patch, strict UTF-8 NUL-free target file, line lengths, hunk count, and resulting bytes are bounded. Envd validates the complete unified diff against exactly `expected_revision`, applies every hunk without newline or Unicode normalization beneath the mount's private staging root, revalidates the sealed candidate's identity and complete digest, and atomically commits all or none. Fuzz matching that could modify an unintended location is not part of the base contract.

### `file.copy`

```python
class FileCopyParams(BaseModel):
    context: EIPCallContext
    source: EIPPath
    destination: EIPPath
    expected_source_revision: FileRevision | None = None
    expected_destination_revision: FileRevision | None = None
    replace: bool = False
    require_atomic_destination: bool = False
    require_stable_source: bool = False


class FileCopyResult(BaseModel):
    destination: FileInfo
    bytes_copied: int
    atomic_destination: bool
    source_stability: Literal["verified", "unverified"]
    receipt: OperationReceipt
```

Copy reads and writes in bounded chunks and separately checks source-read and destination-write authority. Envd pins the source identity, observes a safe source revision when available, and re-observes it after the final source byte. A successful result reports `source_stability="verified"` only when those revisions match; lack of safe revision support reports `unverified` without a snapshot claim. A changed revision never returns an ordinary successful result.

`expected_source_revision` requires safe revision support and must match at open and completion. Supplying it or setting `require_stable_source=true` forces use of an atomic destination candidate so a late source conflict can still abort before publication; lack of safe revision support or the private atomic destination boundary returns `unsupported` before dispatch. `require_atomic_destination=true` independently requests that same publication guarantee without requiring source stability. For an atomic path, copy builds, hashes, revalidates, and commits the complete destination candidate through that mount's private same-filesystem staging root only after source checks pass. Otherwise the operation can use a non-atomic destination and reports `atomic_destination=false`. If a safe source revision changes after a non-atomic copy has exposed bytes, the method returns `conflict` with a receipt describing known destination progress instead of reporting stable success or rollback. The destination never aliases the source through a symlink escape.

Cross-Environment copy is not an EIP method. A trusted client opens one source reader and one destination writer through independently authorized bindings and pumps bounded raw chunks under backpressure; it commits the destination only after source terminal/count/digest verification and successful close. `stability="changed"` aborts the destination, while `unverified` can complete without a snapshot claim and is preserved in the copy outcome. A strict caller requests stable source before transfer so an unverified close aborts rather than rejecting only after publication. Within one envd instance, copying between configured mounts remains one method only when both mounts permit it and no protected or isolation boundary is crossed.

### `file.move`

```python
class FileMoveParams(BaseModel):
    context: EIPCallContext
    source: EIPPath
    destination: EIPPath
    expected_source_revision: FileRevision | None = None
    expected_destination_revision: FileRevision | None = None
    replace: bool = False


class FileMoveResult(BaseModel):
    destination: FileInfo
    receipt: OperationReceipt
```

Move is available only when envd can provide one atomic rename within the same configured mount and filesystem. Cross-mount or copy-then-delete move returns `unsupported` rather than presenting two mutations as atomic. Destination replacement behavior and expected source/destination revisions are explicit.

### `file.remove`

```python
class FileRemoveParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    expected_kind: FileKind
    expected_revision: FileRevision | None = None
    recursive: bool = False
    max_entries: int = 1


class FileRemoveResult(BaseModel):
    removed_entries: int
    receipt: OperationReceipt
```

Removal requires the expected kind and optional expected revision. Directory removal is non-recursive by default. Recursive removal has finite depth and entry limits, never follows symlinks, and is rejected when the implementation cannot preserve protected-path and race guarantees. A recursive partial failure returns a receipt describing known progress; it never claims rollback.

## Local Port Observation

EIP port methods observe local TCP listener readiness inside the selected Environment. They do not allocate a provider ingress route, publish a URL, modify a firewall, create a tunnel, or grant network authority.

```python
type PortAddress = Literal["loopback", "any"]
type PortStatus = Literal[
    "listening", "not_listening", "unknown"
]


class PortTarget(BaseModel):
    protocol: Literal["tcp"]
    address: PortAddress
    port: int


class PortObservation(BaseModel):
    target: PortTarget
    status: PortStatus
    managed_process: ProcessHandle | None
    observed_at: datetime


class PortInspectParams(BaseModel):
    context: EIPCallContext
    target: PortTarget


class PortInspectResult(BaseModel):
    observation: PortObservation


class PortWaitParams(BaseModel):
    context: EIPCallContext
    target: PortTarget
    desired_status: Literal["listening", "not_listening"]


class PortWaitResult(BaseModel):
    observation: PortObservation
```

`port.inspect` returns one observation. `port.wait` uses the `EIPCallContext.deadline` as its finite wait boundary and returns when the desired status is observed or that deadline expires. The base contract follows the Linux/POSIX TCP port domain: `port` is an integer in `1..65535`; port `0` is valid for listener allocation but is never an observable listening target. Availability of `port.observe` and any narrower current policy determine whether the call is admitted; EIP does not define a configurable default port-range grant. Arbitrary remote hosts, UDP scanning, raw sockets, packet capture, and host-network enumeration are not part of this capability.

When the platform can safely attribute a listener to an envd-managed process in the current daemon generation, `managed_process` can be returned. An unmanaged listener is reported only as policy permits and never reveals a PID or identity. `unknown` is used when namespace, platform, or permission prevents trustworthy observation.

A command in isolated Linux `deny` networking has its own empty network namespace and cannot expose an IP listener to envd or the provider. A command using `host` network or an explicit outer sandbox network can be observed only from the network boundary where envd runs. The descriptor reports capability honestly.

Provider adapters own the mapping from a successfully observed local port to a public, tunneled, or container-exposed endpoint. EIP never treats listening status as proof that an external route exists or is authorized.

## Host-driven Ingestion and Delivery

EIP 1.0 does not ask envd to fetch an arbitrary URL. An authenticated Host or product gateway applies its own network, redirect, credential, content, and egress policy, reads the external source incrementally, and writes it through a binary writer. This keeps SSRF-sensitive network authority outside the Environment daemon and gives URL downloads, browser uploads, generated artifacts, and cross-Environment copies the same staged commit path.

A browser is not given the daemon API key or a raw EIP session. A product-facing file gateway authenticates the user, resolves a scoped Environment binding, re-authorizes the path and action, and proxies bytes with bounded memory and backpressure. Browser preview or download uses a binary reader even for `.txt`; an online text editor uses `read_text` and revision-checked `write_text` or `patch_text`. A product HTTP Range request maps to one reader opened with the corresponding byte range.

## Resource Lifetime

Native files and directories are provider Environment state and can remain after envd exits. EIP does not serialize a filesystem snapshot or daemon registry. Process handles, operation records, receipts, output references, text/traversal cursors, and private spool data exist only in the current daemon generation. Recognizable stale staged candidates from a crashed prior generation are internal cleanup artifacts rather than Environment files: before readiness, startup scans only the flat pinned roots, stops at `max_staged_file_objects` or `max_staged_file_bytes`, and deletes within `staging_scavenge_timeout_ms`. Any excess, unexpected shape, timeout, deletion uncertainty, or accounting uncertainty fails startup; envd never recursively walks an inherited arbitrary tree. A fresh authenticated protocol session in the current generation can continue using generation-owned records where their owning contract permits it; a daemon restart cannot.

File reader and writer handles are the deliberate exception to generation-wide reconnectability. They are ephemeral, single-attachment resources bound to the exact initialized session and authenticated data carrier that opened them. Session close, idle expiry, carrier loss, or reconnect closes readers and aborts pre-handoff writers. A handoff-complete writer's operation record and attached receipt/idempotency evidence remain generation-scoped for reconciliation even though its transfer handle is gone.

## Failure Semantics

| Failure                                        | Outcome                                                                                                  | Side-effect meaning                                   |
| ---------------------------------------------- | -------------------------------------------------------------------------------------------------------- | ----------------------------------------------------- |
| Invalid logical path, mount, query, or port    | `invalid_params` or `denied`                                                                             | Pre-dispatch                                          |
| Symlink or canonical target escapes policy     | `denied`                                                                                                 | Pre-dispatch for requested mutation                   |
| File revision or resource precondition changed | `conflict`                                                                                               | No requested commit when detected before commit       |
| Text-result or traversal bound reached         | Explicit cursor/incomplete disposition, quota error, or output-limit error                               | No hidden completeness claim                          |
| Reader interrupted or source revision changed  | Incomplete close, transport failure, or changed completion that the high-level reader maps to `conflict` | Delivered bytes are not reported as verified complete |
| Writer frame, count, or digest mismatch        | `integrity_mismatch`; staged candidate removed                                                           | No destination mutation                               |
| Atomic replacement unsupported                 | `unsupported` before dispatch                                                                            | No destination mutation                               |
| Transport lost before writer commit            | Writer abort and staged-candidate cleanup                                                                | Destination remains unchanged                         |
| Transport lost during or after writer commit   | Receipt or `unknown_outcome` according to commit evidence                                                | Reconcile before retry                                |
| Recursive removal partially completes          | Failed receipt with known progress                                                                       | No rollback claim                                     |
| Port cannot be observed safely                 | `status="unknown"` or `unsupported`                                                                      | No listener mutation                                  |
| Generation-local selector expired or stale     | `retention_gap`, `invalid_handle`, or `stale_generation`                                                 | No fabricated continuation                            |

## Compatibility

File method semantics are capability-gated independently from native platform. New metadata fields can be additive, but changing path normalization, symlink behavior, text decoding, range meaning, transfer-handle scope, data offset or integrity rules, write-mode defaults, commit atomicity, revision scope, search dialect, or traversal ordering requires an incompatible protocol revision.

Providers can expose narrower limits and omit unsupported methods. A client never infers support from operating system, Docker/E2B labels, or daemon package version. Common conformance tests use symlink escapes, concurrent replacement, raw transfer backpressure, interruption cleanup, integrity mismatch, output bounds, mutation preconditions, receipt ambiguity, path-find/content-search distinctions, and cursor invalidation fixtures.

## Invariants

01. Every filesystem operand selects one trusted logical mount and never accepts a native root from request data; a multi-path operation authorizes each operand independently.
02. Lexical validation and native canonicalization both apply; symlinks cannot expand authority across mounts, protected paths, or host roots.
03. Read-only mount policy constrains both file methods and command filesystem grants.
04. Every text page, traversal, query, patch, data frame, file, staged candidate, transfer, result, and duration has a finite bound.
05. Text convenience operations are strict UTF-8 and bounded; arbitrary complete content uses a raw binary reader or writer rather than JSON/base64.
06. One binary reader carries one opened file interval sequentially; only terminal consumer acceptance reports a complete count and digest, while revision stability remains honest and no snapshot isolation is fabricated.
07. One binary writer stages under a pinned, same-filesystem, caller-inaccessible per-mount root; only complete candidate revalidation and an atomic commit can change the destination, and quota is released only after ownership transfer or confirmed deletion.
08. File transfer handles are single-attachment and session-scoped; generation-scoped commit operation records and their attached receipt/idempotency evidence, not transfer resumption, reconcile an ambiguous commit.
09. Atomic mutation is claimed only when the native commit primitive provides it; cross-mount move never masquerades as atomic.
10. Every mutating method returns bounded side-effect evidence and preserves unknown outcome after ambiguous commit response loss.
11. Port methods observe only policy-authorized local TCP targets in `1..65535` and never create external exposure or scan remote hosts.
12. `file.find` matches relative path names and never reads file content; `file.search` matches UTF-8 regular-file content and uses path globs only for file selection.
13. Native files can outlive envd, while process, operation, receipt, output, cursor, transfer, and spool records never outlive their owning session or daemon generation.
14. Provider lifecycle state, transport state, and Host durable execution state never enter an EIP state export because EIP defines no state export or restore method.
