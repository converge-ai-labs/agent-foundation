# File Tool Semantics

## Design Position

The reusable File Toolset is the model-facing projection of one provider-neutral `FileOperator`. It owns the stable `view`, `glob`, and `grep` schemas, model-safe results, progressive disclosure, and optional media-analysis selection. The selected Environment file backend owns path confinement, traversal, ignore evaluation, content scanning, cancellation, and operation limits.

The Harness never obtains a native path, shells out to a search executable, or pulls complete remote files into the Agent process to emulate an unavailable search operation. Direct Local and EIP implement the same semantic requests. A backend that cannot honor a selected request fails explicitly rather than silently changing ordering, ignore rules, media handling, or resource scope.

## Ownership

| Concern                                                                   | Owner                                                       |
| ------------------------------------------------------------------------- | ----------------------------------------------------------- |
| Tool names, arguments, model result shape, and disclosure                 | File Toolset                                                |
| Logical-path resolution and revision-pinned authorization                 | Environment core                                            |
| Traversal, ignore evaluation, matching, context assembly, and scan limits | Selected `FileOperator` backend                             |
| Native media compatibility policy                                         | Trusted Agent definition                                    |
| Media analysis model, credentials, retry, and provider behavior           | Fresh Host-selected analyzer                                |
| Analyzer usage attribution                                                | File Toolset through `AgentContext.record_provider_usage()` |
| Durable files, indexes, caches, and benchmark history                     | Host or provider                                            |

File contents, names, ignore files, and analysis text are untrusted model context. None can grant Environment access, select a model, modify policy, or become durable authority.

## `view`

Text reads remain bounded line pages over `FileOperator.read_text()`. A file page preserves source line offsets, reports truncated lines and continuation, and never reads the complete file merely to calculate totals. Published skill paths and typed `FILE_VIEW_RULES` may widen finite page and disclosure limits as defined by [Context and Memory](09-context-and-memory.md#skills-and-discovery).

Common image, audio, and video files use an explicit definition-selected policy:

```python
type FileMediaMode = Literal["native", "analyze", "reject"]


class FileViewMediaConfiguration(BaseModel):
    image: FileMediaMode
    audio: FileMediaMode
    video: FileMediaMode
    max_image_bytes: int
    max_audio_bytes: int
    max_video_bytes: int
    analysis_deadline_seconds: float
```

The mode has these exact meanings:

- `native` reads one bounded file value and returns native Pydantic media in `ToolReturn`;
- `analyze` reads one bounded file value, invokes the fresh analyzer with its canonical media type, logical source reference, and optional focused instructions, then returns bounded text;
- `reject` returns a typed unsupported-media tool error without reading file bytes.

The Harness does not infer a mode from a model name or probe a provider by first sending unsupported media. The Host aligns this configuration with the definition's `ContentFilterConfiguration` and model route. A dynamic resolver that selects incompatible media surfaces selects a compatible Agent definition or trusted route policy; model content never widens the mode.

Analysis uses a narrow run-scoped port:

```python
class FileMediaAnalysisRequest(BaseModel):
    kind: Literal["image", "audio", "video"]
    media_type: str
    data: bytes
    source: EnvironmentPath
    instructions: str | None


class FileMediaAnalysisResult(BaseModel):
    text: str
    usage: tuple[ProviderUsage, ...]


class FileMediaAnalyzer(Protocol):
    async def analyze(
        self,
        request: FileMediaAnalysisRequest,
    ) -> FileMediaAnalysisResult: ...
```

The definition-selected file feature requires one fresh run Capability carrying `FileMediaAnalyzer` when any family uses `analyze`. The analyzer is active behavior and therefore never enters passive `ToolRuntimeMetadata`. Its model construction, provider credentials, transport, internal prompts, and retry policy remain outside `FileToolset`. Every returned usage record is validated and attributed to the current tool call. Timeout, invalid output, or analyzer failure returns a typed failure and never falls back to native media, because native delivery would violate the selected compatibility policy.

PDF and other document formats remain owned by the Document Capability. File `view` returns a typed conversion-required result and does not guess a parser from an extension.

## `glob`

`glob` sends one bounded path query to the selected backend. A bare pattern matches a basename at any descendant depth; a pattern containing `/` matches the complete path relative to `root`; a leading `/` anchors at `root`; and `**` spans complete path segments. Hidden components are excluded by default, except that an explicitly selected hidden `root` remains reachable. `include_hidden=true` admits all hidden descendants within the authorized root.

`include_ignored=false` selects Git-compatible ignore evaluation rooted at the query root. The backend reads applicable `.gitignore` files while traversing, applies parent-to-child precedence and negation, and prunes an ignored directory only when no applicable negation can re-include a descendant. `include_ignored=true` disables ignore evaluation. Ignore files outside the authorized root have no effect.

Results use deterministic relative-path byte ordering. Modification time is metadata, not ordering authority: mtime ordering is unstable under concurrent writes and forces complete-result retention before a first page. Pagination is an observation over current state and does not promise snapshot isolation.

## `grep`

`grep` sends one bounded content-search request containing the regular expression, include glob, hidden and ignore modes, context width, file limit, per-file match limit, global match limit, maximum file bytes, preview length, and page position. The backend performs traversal, filtering, matching, and before/after context assembly in that operation.

The Harness does not request a broad content page and then filter it locally. It does not issue one `read_text` operation per match. It only maps the backend result into the stable model-facing records and applies progressive disclosure. Therefore remote bytes scale with the bounded result page and context, not with scanned source bytes.

A match represents one matching LF-delimited UTF-8 line, even when the expression matches several occurrences on that line. Results are ordered by relative path and then line number. Invalid UTF-8, NUL-containing, special, symlinked, over-limit, and unreadable files follow the backend's typed skip or failure contract; they are never lossily decoded as successful text. Context is clipped at file boundaries and carries its one-based start line.

## Backend Contract

The provider-neutral query contracts carry all semantics that affect observable results or asymptotic transport behavior. `FileQueryRequest` includes ignore selection. `FileTextSearchRequest` additionally includes the file include pattern, context, and finite file/per-file/byte limits. `FileTextSearchResult` returns context and bounded skip facts in the same response as matches.

Direct Local runs one complete bounded query or search in one worker-thread call. It may use process-private indexes or optimized libraries, but those are replaceable implementation details. EIP maps the same values to `file.find` and `file.search`; agent-envd performs traversal and content scanning inside the selected mount and streams bounded chunks. Neither backend follows a symlink outside the selected root.

An implementation enforces finite pattern bytes, traversal entries and depth, files considered, bytes per file and operation, matches per file and operation, context lines, result bytes, and duration. Cancellation stops traversal and returns a typed error rather than a partial success page. Provider denial always narrows the Toolset request.

## Performance Invariants

The normal execution path has these testable properties:

1. one `glob` page uses one backend query operation;
2. one `grep` page uses one backend search operation, independent of returned match count;
3. adding context does not add backend round trips per match;
4. ignored directories are pruned during traversal rather than filtered after complete enumeration when the selected ignore mode permits pruning;
5. EIP transfers bounded match records and context, never complete searched files;
6. Direct Local keeps blocking traversal and matching off the event loop.

Repository benchmarks compare Direct Local with the platform `rg` baseline and compare EIP end-to-end time and bytes with the same agent-envd local operation. Benchmarks use warm medians over fixed small-source, monorepo-shaped ignored-tree, and remote-transport fixtures. Semantic conformance remains the merge gate; performance thresholds become gating only from measured stable CI baselines rather than machine-specific absolute time.

## Compatibility and Failure

Tool names remain `view`, `glob`, and `grep`. Media mode, ignored-path behavior, ordering, and richer search pages are observable compatibility contracts. Hosts must not change them for a resumed definition without selecting a compatible revision.

An EIP peer negotiates support for the complete enhanced find/search request before exposure. Missing support fails with `environment_unsupported`; the client never emulates the request by downloading files or silently dropping fields. Ordinary provider failures, limits, stale bindings, cancellation, and analysis failures retain their typed Environment or tool errors and retry hints.
