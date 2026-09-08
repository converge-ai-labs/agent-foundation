# Resource Operations

## Design Position

`a13n-envd` exposes semantic, mount-scoped filesystem operations and bounded observation of local listening ports. It resolves native paths and races inside the Environment boundary rather than exposing host absolute paths or asking the EIP client to emulate filesystem behavior through low-level syscalls.

Every session observes the daemon's immutable trusted mount configuration. The descriptor exposes those logical mounts and one optional `root_mount_id`; a caller cannot add a native root or select a broader server-filesystem mode. An operator that intentionally needs whole-filesystem breadth configures `/` or explicit platform volume roots as ordinary mounts. Every request still uses platform-neutral `EIPPath` values rather than native host paths.

## Boundaries

| Concern                                                                              | Owner                                                    | Relationship                                            |
| ------------------------------------------------------------------------------------ | -------------------------------------------------------- | ------------------------------------------------------- |
| Configured roots, writable ceilings, allowed methods, and port-observation policy    | Operator or provider adapter                             | Trusted immutable daemon configuration                  |
| Harness virtual `/workspace` and `/environment/{alias}` routing                      | Harness                                                  | Resolves to one Environment adapter before EIP dispatch |
| Logical mount paths and file, search, and port methods                               | This document                                            | Stable EIP resource contract                            |
| Native path canonicalization, symlink containment, bounded publication, and receipts | `a13n-envd`                                              | Authoritative provider enforcement                      |
| Provider ingress, public URL, tunnel, or container port publishing                   | Provider adapter                                         | Outside EIP port observation                            |
| Raw file-transfer framing, attachment, and backpressure                              | [Transports and Sessions](03-transports-and-sessions.md) | Carries bytes for the transfer lifecycle owned here     |

Filesystem selectors and port numbers are not authority. Every method also crosses trusted-session checks, exact method availability, current generation, mount policy, and configured ceilings.

## Mount Model

Trusted daemon configuration uses this conceptual, non-EIP shape:

```python
class TrustedMountConfig(BaseModel):
    mount_id: str
    native_root: str
    writable: bool
    allow_command_execution: bool = True
    max_file_bytes: int
    allowed_operations: frozenset[str]
```

`native_root` is operator-only secret-adjacent configuration: envd canonicalizes it, validates policy against that canonical path, opens the final component without following a link, and retains the opened directory as the mount's capability root. The launching provider must keep the root's containing directory outside payload or other untrusted mutation during that startup sequence; after open, renaming the native path does not retarget the retained capability. This bootstrap authority precondition replaces any portable FileID requirement. Envd never returns the native root in EIP. `allowed_operations` is a subset of exact file, search, command-cwd, and executable-source operations supported by the daemon. `writable=false` is an absolute ceiling for file mutations and required-isolation command grants. A writable mount does not assert exclusive control over contents: commands, provider tooling, and other processes with native access can mutate them concurrently.

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
    max_file_bytes: int
```

`mount_id` is a bounded stable logical ID within one Environment generation. `logical_root` is a display-only EIP path such as `/`; it is not a host path. `path` is absolute within that logical mount, begins with `/`, uses `/` separators on the wire, contains no NUL, and is validated lexically before native resolution. `/` is the mount root; otherwise empty interior segments, a trailing separator, `.`, and `..` are rejected instead of normalized into another request.

A mount configuration binds the logical ID to one canonical native directory, read-only or read-write ceiling, protected-path checks, exact allowed operations, mutation-size bounds, and platform semantics. `supports_atomic_replace=true` means envd can build and integrity-check a complete candidate in the destination directory and publish it with one same-filesystem atomic rename. It does not mean envd exclusively owns the destination, serialize external writers, or provide compare-and-swap. The descriptor reports observed behavior but cannot widen configuration or OS authority.

Configured mount roots are non-overlapping after canonicalization unless exact aliases intentionally share identical policy and one logical identity. Ancestor/descendant roots with competing policies are rejected at startup. `max_file_bytes` bounds complete mutation candidates; it does not reject metadata reads, a bounded reader range, or incremental search merely because the source regular file is larger. Those operations use their own transfer, response, work, and duration ceilings.

### Protected runtime subtraction

Generation-private runtime, output spool, connector/bootstrap state, supervisor control, probe sentinels, logs, installed helpers, and credential-bearing roots are protected even when a configured mount is their native ancestor. Envd opens or fixes each protected root during startup and derives a resource-layer deny set from native identities and canonical topology. Choosing a runtime parent outside ordinary narrow mounts is preferred but never substitutes for this enforcement because an intentional `/` or volume-root mount physically contains almost every daemon path.

Every file method applies protected-root subtraction independently of command isolation. A direct selection of a protected root or descendant is denied without disclosure. Component resolution refuses a symlink, junction, mount point, reparse point, or replaced parent that would enter a protected identity. `file.list`, `file.find`, `file.search`, and recursive mutation test an entry before returning protected metadata or descending, so traversal cannot enumerate names below the boundary. Staged candidates remain destination-local but cannot be created inside a protected root through EIP authority.

Startup validates mount/protected-root topology and the exact platform-relative implementation. A broad mount is unavailable when the platform/filesystem cannot provide no-follow protected subtraction for all advertised operations. Required execution isolation performs a separate subtraction for payload commands; passing one layer never substitutes for the other.

## Canonicalization and Symlinks

For every operation, envd:

1. selects the trusted mount by exact `mount_id`;
2. validates the lexical wire path and operation-specific size bounds;
3. resolves components relative to the already opened mount capability root;
4. checks every followed symlink target remains inside the same authorized root and outside protected paths;
5. applies read/write and operation policy to the resolved resource;
6. uses capability-relative and no-follow native operations at mutation boundaries;
7. revalidates resource shape and publication intent at the latest practical boundary before the native mutation.

A symlink can narrow convenience but cannot expand authority. A link inside a writable workspace that targets daemon state, another mount, the host home, or any unexposed path remains inaccessible. The daemon does not return a native canonical path to the client.

`file.stat` can inspect the link itself or a contained target through an explicit `follow_symlinks` flag. Read, copy-source, and patch operations follow contained symlinks by default. List, find, search, and recursive remove report a symlink or reparse-point entry but never descend through it. Write replacement does not replace an out-of-root target, and `file.remove` removes the selected directory entry itself. Recursive traversal has finite depth and entry ceilings and therefore does not require a portable native file identity.

Canonicalization failure is pre-dispatch for the requested filesystem mutation. If resource shape or publication intent no longer holds at a checked boundary, envd returns a conflict or denial; it never silently retargets or retries the request. Envd does not claim compare-and-swap or serialize commands and external native writers, so a concurrent writer can still change a path before or after any observation.

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
```

EIP exposes current file metadata but no file-version or revision token. Native identity, timestamps, size, and content can all change concurrently and are not combined into a portable version abstraction. Timestamps and executable bits are observations with platform-specific precision. EIP does not expose owner IDs, ACL internals, native inode numbers, extended attributes, device nodes, sockets, or arbitrary platform metadata through the base file contract.

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

`file.read_text` is the bounded UTF-8 convenience operation used by editors and Agent file tools. It is line-oriented rather than a byte-range facade.

```python
class FileReadTextParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    line_offset: int = 0
    line_limit: int
    max_line_length: int


class FileReadTextResult(BaseModel):
    info: FileInfo
    text: str
    line_offset: int
    lines_read: int
    has_more: bool
    truncated_lines: tuple[int, ...]
```

`line_offset` is the zero-based number of LF-delimited logical lines to skip. LF (`0x0A`) ends a line; CRLF is preserved as part of that LF-terminated line, while a bare CR, VT, FF, NEL, U+2028, or U+2029 remains ordinary line content. A non-empty suffix after the last LF is one final line. `line_limit` and `max_line_length` are finite positive request bounds. The daemon preserves line endings, decodes strictly as UTF-8, and never performs lossy replacement, newline normalization, MIME inference, or binary-to-text coercion.

A source line longer than `max_line_length` remains readable: the response retains its character prefix and its LF terminator when present, and records the one-based source line number in `truncated_lines`. The daemon scans incrementally, retains only bounded selected prefixes, and stops after it can determine `has_more`; it does not read or hash the complete file merely to return one page. Invalid UTF-8 or NUL encountered while scanning the requested page returns `unsupported`.

`lines_read` counts returned logical lines and `has_more` reports whether another line existed after them. A caller continues with `line_offset + lines_read`. Every call is an independent current-filesystem observation: concurrent insertion, deletion, or replacement can shift a later result, and EIP provides no text cursor, snapshot, revision, or stability promise.

### Binary reader

Raw file content uses a session-scoped transport reader. The reader is a low-level, session-scoped EIP mechanism.

```python
class FileReaderHandle(RootModel[str]): ...


class ContentDigest(BaseModel):
    algorithm: Literal["sha256"]
    value: str


class FileByteRange(BaseModel):
    offset: int = 0
    length: int | None = None


class FileReaderOpenParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    byte_range: FileByteRange | None = None
    transfer_timeout_ms: int | None = None


class FileReaderOpenResult(BaseModel):
    reader: FileReaderHandle
    info: FileInfo
    expires_at: datetime


class FileReadCompletion(BaseModel):
    produced_bytes: int
    digest: ContentDigest


class FileReaderCloseParams(BaseModel):
    context: EIPCallContext
    reader: FileReaderHandle


class FileReaderCloseResult(BaseModel):
    completion: FileReadCompletion
```

`offset` and `length` count bytes, but `length` is a maximum, not an exact-read assertion. The producer starts at `offset` and stops at source EOF or the requested maximum. Zero length, offset at or beyond EOF, and EOF before `length` produce a clean empty or short stream. Source read failure resets the transfer. Omitting `length` selects bytes available from `offset` subject to `max_file_transfer_bytes`; a caller reads a larger file through explicit bounded ranges rather than a server cursor. Later growth is not added, while later shrinkage can produce a shorter successful stream. The range is an observation bound, not a snapshot or exact-length promise.

The call context timeout bounds opening only. `transfer_timeout_ms` is a positive relative duration narrowed by daemon transfer policy and converted to a monotonic deadline at open. `expires_at` reports the resulting current absolute observation for diagnostics; it is not caller-supplied time or a lease. Valid progress can refresh an internal idle timer without extending the transfer-duration ceiling.

After open, the carrier attaches exactly one server-to-client stream. Attachment offsets start at zero regardless of native offset. Envd emits `END` after clean producer termination. Readers never use `END_ACK`; successful `file.close_reader` is the sole consumer-acceptance action and is legal only after the public consumer drained every chunk. A client computes count and SHA-256 while consuming, then compares its evidence with the close result. Early exit sends `RESET` when possible and performs no close acceptance.

`produced_bytes` and the required SHA-256 digest describe bytes delivered by the accepted stream. They are transfer-integrity evidence only and do not prove immutable source content, source EOF position, pathname stability, or a filesystem snapshot.

A reader awaiting close remains active and bounded by session and transfer lifetime. Reader handles cannot cross sessions, survive reconnect, or become command-output references.

### `file.list`

```python
class FileListParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    offset: int = 0
    max_results: int = 100
    include_hidden: bool = False


class FileListEntry(BaseModel):
    relative_path: str
    info: FileInfo


class FileListResult(BaseModel):
    entries: tuple[FileListEntry, ...]
    offset: int
    has_more: bool
    omitted_unrepresentable_entries: int
```

`file.list` returns only immediate children. Representable entries are ordered bytewise by relative path. `offset` is the zero-based number of ordered representable entries to skip, `max_results` is positive, and `has_more` reports whether another representable entry existed. Hidden names are omitted unless requested. Native names that cannot be represented safely in portable UTF-8 EIP paths are omitted rather than lossily decoded; `omitted_unrepresentable_entries` reports the bounded count observed during that page's traversal.

### `file.find`

`file.find` searches descendant path names without opening regular-file content.

```python
class FileFindParams(BaseModel):
    context: EIPCallContext
    root: EIPPath
    pattern: str
    offset: int = 0
    max_results: int = 100
    recursive: bool = True
    include_hidden: bool = False
    kinds: tuple[FileKind, ...] = ()
    respect_git_ignore: bool = False


class FileFindResult(BaseModel):
    entries: tuple[FileListEntry, ...]
    offset: int
    has_more: bool
    omitted_unrepresentable_entries: int
```

The glob matches each descendant's `/`-separated path relative to `root`; `root` itself is not a result. A bare pattern without `/`, such as `*.py`, matches the basename at any traversed depth. A pattern containing `/` matches the complete relative path, and a leading `/` explicitly anchors that path at `root`. The supported syntax is `*`, `?`, `**`, bracket character classes, and non-nested brace alternatives such as `*.{py,rs}` or `{src,tests}/**/*.py`. Brace groups contain at least two nonempty alternatives; at most 256 expanded patterns and 16 KiB of input are accepted. Each expanded pattern follows the same anchoring rules. Backslash escapes, nested braces, numeric brace ranges, and embedded `**` (rather than a complete segment) are not supported. Invalid patterns return `invalid_params` with `field` and a stable `safe_detail` reason; an empty result is success, not an invalid pattern. Within a path pattern, `*`, `?`, and classes do not cross `/`, while `**` matches complete path segments. A terminal `/**` selects descendants, not the prefix path itself. Character classes support `!` or `^` negation, literal `[` in `[[]`, and literal `]` in `[]]`; empty or descending ranges are invalid. An empty `kinds` tuple accepts every file kind. `recursive=false` restricts the operation to immediate children. `respect_git_ignore=true` interprets bounded nested `.gitignore` files from the selected root downward and prunes ignored directories during traversal; hidden-name selection remains independent. Results share `file.list` ordering and offset semantics.

### `file.search`

`file.search` searches UTF-8 regular-file content and emits one result per matching LF-delimited line, not one result per occurrence.

```python
class FileSearchParams(BaseModel):
    context: EIPCallContext
    root: EIPPath
    query: str
    mode: Literal["literal", "regex"]
    case_sensitive: bool = True
    offset: int = 0
    max_results: int = 100
    include_hidden: bool = False
    max_line_length: int
    include_pattern: str = "**/*"
    respect_git_ignore: bool = False
    context_lines: int = 0
    max_matches_per_file: int | None = None
    max_files: int | None = None
    max_file_bytes: int = 64 * 1024 * 1024


class FileSearchMatch(BaseModel):
    path: EIPPath
    line_number: int
    preview: str
    preview_truncated: bool
    context: str = ""
    context_start_line: int = 1


class FileSearchResult(BaseModel):
    matches: tuple[FileSearchMatch, ...]
    offset: int
    has_more: bool
    omitted_unrepresentable_entries: int
```

Search recursively considers regular files under `root`, omitting hidden path components unless requested. `include_pattern` applies the same path-glob semantics as `file.find`. `respect_git_ignore=true` applies bounded nested ignore files during traversal and prunes ignored directories. A file containing invalid UTF-8 or NUL is skipped deterministically. Literal and regex matching are applied independently to each complete logical line and cannot span LF boundaries. `case_sensitive=false` uses explicit Unicode case-insensitive matching.

`line_number` is one-based. `preview` is the containing line with only its LF terminator removed and at most `max_line_length` characters; `preview_truncated` reports an omitted suffix. `context` contains the bounded preview rendering of up to `context_lines` preceding and following lines, preserves available LF terminators, and starts at `context_start_line`. EIP does not expose a raw byte offset or duplicate the same line for several occurrences. Results are ordered by relative path and then line number.

Pattern size, incremental traversal work, bytes scanned per file and operation, result count, per-file match count, eligible files searched, source-file bytes, context width, preview length, response bytes, and duration are finite. `max_files` counts included regular files actually opened for search; traversal stops when the ceiling is reached. Files larger than `max_file_bytes` are skipped before that count. A source file can exceed the mutation candidate limit when the explicit search ceiling permits it; search streams bounded lines and applies its independent per-file and operation scan ceilings. `offset` skips ordered matching lines, `max_results` bounds the page, and `has_more` reports another match in that observation. Implementations page deterministically without retaining a complete traversal/result set merely to return the first page. Unrepresentable path entries are omitted and counted. A response ceiling can narrow result count; if the first selected item cannot fit, the method returns `output_limit_exceeded`. Cancellation and timeout return typed errors rather than partial success.

Neither find nor search follows a symlink outside the selected mount or reads special files. Result text remains untrusted caller-visible content.

## Mutation Operations

All mutations return an `OperationReceipt` and resulting current metadata when applicable. A method validates its semantic request before native mutation. Operation-ID replay, conflict, and unknown-outcome rules follow [EIP Protocol](02-eip-protocol.md#common-operation-context-and-replay).

File mutation modes express publication intent, not global concurrency control. Envd serializes only its own critical sections where required for internal ownership; commands and external native writers do not participate in that lock. A complete-candidate rename prevents readers from observing envd's partial candidate bytes, but another writer can modify or replace the destination before or after publication. `create` uses native no-replace publication when supported. `replace`, `upsert`, append, patch, copy, move, and remove validate their ordinary shape requirements at the latest practical boundary, but they do not compare a caller-supplied revision or promise a total order across all writers. When concurrent publications race, the filesystem's completion order determines the final entry, and the caller must read or reconcile afterward when the final state matters.

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
    executable: bool | None = None


class FileWriteTextResult(BaseModel):
    info: FileInfo
    bytes_written: int
    receipt: OperationReceipt
```

The exact UTF-8 encoding of `text` is bounded by request, response-independent file, and mount limits before staging. Envd rejects NUL and performs no newline, Unicode-normalization, encoding, or BOM transformation. `create` intends absence, `replace` intends an existing regular file, `upsert` allows either, and `append` produces a replacement containing the bytes envd reads followed by the supplied bytes. Append requires an existing strict UTF-8, NUL-free file. Repeating the same operation ID and semantic digest replays the accepted operation rather than appending twice. The parent directory must already exist; callers use the separately receipted `file.mkdir` operation rather than hiding directory creation inside a write.

Every mode stages a complete candidate with a bounded random name in the destination directory. The candidate can be visible to commands or other native actors that can list or mutate that directory; it is neither a confidentiality boundary nor a lock. Envd records the digest of the intended complete bytes while building the candidate, rehashes the held candidate immediately before one same-filesystem directory-entry publication, and treats the native rename result as the publication boundary. It does not require a portable native identity token to bind that open handle to the candidate name: an independently authoritative native writer can replace the named entry or destination before or after publication, just as it can replace the destination directly. The digest proves the bytes envd received and verified, not exclusive control of shared native directory state. Integrity failure or a cancellation proven before publication removes the candidate and leaves the destination unchanged unless another native actor independently mutates those entries. A daemon crash can leave a recognizable candidate as ordinary Environment state; no startup-wide filesystem scan is required. Carrier loss after method acceptance does not cancel this mutation; the client reconciles its operation ID, receipt, or observed resource state before starting another operation.

### Binary writer

A raw upload uses one session-scoped binary writer. The destination is not changed by open, data attachment, or data-stream completion.

```python
class FileWriterHandle(RootModel[str]): ...


class FileWriterOpenParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    mode: FileWriteMode
    executable: bool | None = None
    transfer_timeout_ms: int | None = None


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

`file.open_writer` authorizes the destination, reserves finite transfer/staging capacity, and creates one complete-file candidate in the destination directory without changing the target. The parent must already exist. For append, the candidate begins with the currently observed existing bytes. A candidate can be visible to another actor that already controls that directory; it is not a confidentiality boundary or lock. `max_transfer_bytes` bounds newly uploaded bytes, while the final candidate also obeys the mount mutation ceiling.

The transport attaches one client-to-server stream. Offsets start at zero and cover only uploaded bytes. Envd writes bounded contiguous chunks while counting and hashing them. Client `END` followed by envd `END_ACK` seals the upload but does not publish it.

`file.commit_writer` first verifies the sealed upload's exact byte count and digest. Commit acceptance atomically decides candidate ownership: if session close wins first, the session deletes the candidate and no commit is dispatched; if commit wins, later session cleanup cannot delete it. Envd then verifies the complete held candidate and current destination policy before one native publication. Create uses no-replace publication; replace/upsert/append publish one complete candidate according to their stated intent. This prevents envd readers from observing envd's partial upload but does not provide compare-and-swap against independent native writers.

`file.abort_writer`, transfer failure, expiry, or session loss removes a candidate only before commit accepts ownership. After acceptance, abort reports `commit_in_progress` or `already_committed`, and response loss is reconciled through the commit operation ID. Cleanup failure remains charged and returns an explicit cleanup error; retry algorithms and thresholds are implementation policy.

`transfer_digest` proves the bytes envd accepted and verified in the held candidate. For append it covers the uploaded suffix while envd separately verifies the staged prefix. It is not a file-version token or a claim that a concurrently writable pathname still contains those bytes.

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


class FilePatchTextResult(BaseModel):
    info: FileInfo
    hunks_applied: int
    receipt: OperationReceipt
```

The patch, strict UTF-8 NUL-free target file, line lengths, hunk count, and resulting bytes are bounded. Envd reads one observed target, validates the complete unified diff against those bytes, applies every hunk without newline or Unicode normalization to a destination-local candidate, revalidates the held candidate's complete size and digest, and performs one native publication. Fuzz matching that could modify an unintended location is not part of the base contract. A concurrent native writer can still mutate the candidate entry or publish another destination version before or after envd.

### `file.copy`

```python
class FileCopyParams(BaseModel):
    context: EIPCallContext
    source: EIPPath
    destination: EIPPath
    replace: bool = False


class FileCopyResult(BaseModel):
    destination: FileInfo
    bytes_copied: int
    receipt: OperationReceipt
```

Copy reads and writes in bounded chunks and separately checks source-read and destination-write authority. It copies the bytes observed through one opened source object but makes no portable source-version or immutable-snapshot claim. Every successful copy publishes one complete destination-local candidate; publication strategy is an envd invariant, not a caller option or result flag. Failure before publication removes the candidate and leaves the destination unchanged. The destination never aliases the source through a symlink escape.

Cross-Environment copy is not an EIP method. A trusted client pumps one provider byte stream into one independently authorized destination stream write under backpressure. Its EIP adapter still verifies transport count and digest internally, while the provider-neutral copy contract treats normal source iterator exhaustion as success and commits only then. Within one envd instance, copying between configured mounts remains one method only when both mounts permit it and no protected boundary is crossed.

### `file.move`

```python
class FileMoveParams(BaseModel):
    context: EIPCallContext
    source: EIPPath
    destination: EIPPath
    replace: bool = False


class FileMoveResult(BaseModel):
    destination: FileInfo
    receipt: OperationReceipt
```

Move is available only when envd can provide one atomic rename within the same effective mount and filesystem. Cross-mount or copy-then-delete move returns `unsupported` rather than presenting two mutations as atomic. Destination replacement intent is explicit, but another native writer can race before or after the rename.

### `file.remove`

```python
class FileRemoveParams(BaseModel):
    context: EIPCallContext
    path: EIPPath
    expected_kind: FileKind
    recursive: bool = False
    max_entries: int = 1


class FileRemoveResult(BaseModel):
    removed_entries: int
    receipt: OperationReceipt
```

Removal requires the expected kind. Directory removal is non-recursive by default. Recursive removal has finite depth and entry limits, never follows symlinks or reparse points, and revalidates entry shape before each native removal. It does not serialize commands or external writers and therefore makes no portable file-identity or compare-and-swap claim. A recursive partial failure returns a typed error with its failed receipt and the exact successfully removed prefix in `EIPErrorData.emitted_items`; it sets `retry_hint=reconcile_first` and never claims rollback.

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

`port.inspect` returns one observation. `port.wait` uses the effective relative `EIPCallContext.timeout_ms` as its finite wait boundary and returns when the desired status is observed or that deadline expires. The base contract follows the Linux/POSIX TCP port domain: `port` is an integer in `1..65535`; port `0` is valid for listener allocation but is never an observable listening target. Exact availability of `port.inspect` or `port.wait` and any narrower current policy determine admission; EIP does not define a configurable default port-range grant. Arbitrary remote hosts, UDP scanning, raw sockets, packet capture, and host-network enumeration are not methods.

When the platform can safely attribute a listener to an envd-managed process in the current daemon generation, `managed_process` can be returned. An unmanaged listener is reported only as policy permits and never reveals a PID or identity. `unknown` is used when namespace, platform, or permission prevents trustworthy observation.

A command under required `deny` networking has no usable IP path through the platform backend and cannot expose an ordinary listener to envd or the provider. A command using `host` or explicit outer-sandbox networking is observable only from the network boundary where envd runs. The descriptor reports the applicable port methods and isolation posture honestly.

Provider adapters own the mapping from a successfully observed local port to a public, tunneled, or container-exposed endpoint. EIP never treats listening status as proof that an external route exists or is authorized.

## Resource Lifetime

Native files and directories are provider Environment state and can remain after envd exits. EIP does not serialize a filesystem snapshot or daemon registry. Process handles, operation records, receipts, output references, and private spool data exist only in the current daemon generation. Because complete candidates are destination-local, a crash can leave a bounded-name candidate beside its intended destination. It is ordinary visible Environment state after daemon ownership is lost; envd does not scan an arbitrary filesystem tree at startup or claim it can distinguish every old candidate from a user-created file safely. A fresh authenticated protocol session in the current generation can continue using generation-owned records where their owning contract permits it; a daemon restart cannot.

File reader and writer handles are the deliberate exception to generation-wide reconnectability. They are ephemeral, single-attachment resources bound to the exact initialized session and authenticated data carrier that opened them. Session close, idle expiry, carrier loss, or reconnect closes readers and aborts pre-handoff writers. A handoff-complete writer's operation record and attached receipt evidence remain generation-scoped for reconciliation even though its transfer handle is gone.

## Failure Semantics

| Failure                                        | Outcome                                                   | Side-effect meaning                              |
| ---------------------------------------------- | --------------------------------------------------------- | ------------------------------------------------ |
| Invalid logical path, mount, query, or port    | `invalid_params` or `denied`                              | Pre-dispatch                                     |
| Symlink or canonical target escapes policy     | `denied`                                                  | Pre-dispatch for requested mutation              |
| Publication intent no longer holds             | `conflict`                                                | No requested publication when detected before it |
| Text-result or structured-result bound reached | `has_more=true` or `output_limit_exceeded`                | No hidden completeness claim                     |
| Reader interrupted or source read fails        | Reset or carrier/source failure                           | Delivered bytes are not reported as complete     |
| Writer frame, count, or digest mismatch        | `integrity_mismatch`; staged candidate removed            | No destination mutation                          |
| Atomic replacement unsupported                 | `unsupported` before dispatch                             | No destination mutation                          |
| Transport lost before writer commit            | Writer abort and staged-candidate cleanup                 | Destination remains unchanged                    |
| Transport lost during or after writer commit   | Receipt or `unknown_outcome` according to commit evidence | Reconcile before retry                           |
| Recursive removal partially completes          | Failed receipt with known progress                        | No rollback claim                                |
| Port cannot be observed safely                 | `status="unknown"` or `unsupported`                       | No listener mutation                             |
| Generation-local selector expired or stale     | `invalid_handle` or `stale_generation`                    | No fabricated continuation                       |

## Compatibility

File method availability is reported by exact JSON-RPC method independently for each platform. New metadata fields can be additive, but changing path normalization, symlink behavior, text decoding, range meaning, transfer-handle scope, data offset or integrity rules, write-mode defaults, commit atomicity, metadata meaning, search dialect, or traversal ordering requires an incompatible protocol revision.

Providers can expose narrower limits and omit unsupported methods. A client never infers support from operating system, Docker/E2B labels, or daemon package version. Common conformance tests use configured mounts, symlink/reparse escapes, concurrent replacement, raw transfer backpressure, interruption cleanup, integrity mismatch, output bounds, publication intent, receipt ambiguity, path-find/content-search distinctions, omission counts, and explicit-offset fixtures.

## Invariants

01. Every filesystem operand selects one effective logical mount and never accepts a native root from request data; a multi-path operation authorizes each operand independently.
02. Lexical validation and native canonicalization both apply; symlinks and reparse points cannot expand authority beyond a configured mount or enter a protected daemon root.
03. Configured read-only mount policy constrains both file methods and command filesystem grants; whole-filesystem breadth requires an explicit ordinary mount, resource-layer protected-root subtraction, and independent command-layer subtraction, never a session mode.
04. Every text result, traversal, query, patch, data frame, file, staged candidate, transfer, result, and duration has a finite bound.
05. Text convenience operations are strict UTF-8 and bounded; arbitrary complete content uses a raw binary reader or writer rather than JSON/base64.
06. One binary reader carries one opened file interval sequentially; only terminal consumer acceptance reports a complete count and digest, while no file-version or snapshot-isolation claim is fabricated.
07. One binary writer stages a bounded complete candidate in the destination directory; the candidate is not private or a lock, and publication never exposes envd's partial bytes.
08. File transfer handles are single-attachment and session-scoped; generation-scoped commit operation records and attached receipt evidence, not transfer resumption, reconcile an ambiguous commit.
09. Atomic publication is claimed only for one complete-candidate rename or native move primitive; it never implies global ordering or compare-and-swap, and cross-mount move never masquerades as atomic.
10. Every mutating method returns bounded side-effect evidence and preserves unknown outcome after ambiguous commit response loss.
11. Port methods observe only policy-authorized local TCP targets in `1..65535` and never create external exposure or scan remote hosts.
12. `file.find` matches relative path names and never reads file content; `file.search` matches UTF-8 regular-file content and uses path globs only for file selection.
13. Native files and crash-left destination-local candidates can outlive envd, while process, operation, receipt, command-output, transfer, and spool records never outlive their owning session or daemon generation.
14. Environment state, transport state, and Host durable execution state never enter an EIP state export because EIP defines no state export or restore method.
