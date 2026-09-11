# Native Host Computer Sharing

## Design Position

Host Files, Git-aware Changes, and Host Terminal let people operate the machine running WebUI without a separate SSH connection. They support code reading, editing, testing, and ordinary development. The App owns these human-facing native operations independently of Agent Runs and Environment Providers.

Host means the server OS and its user account. When the server runs in the Harness UI Docker image, Host means the container and explicitly mounted directories. It never means the browser's machine. Selecting a remote Agent Environment does not retarget these surfaces.

## Enablement and Authority

`a13n-harness-ui webui` enables computer sharing by default for that instance. `--no-share-computer` disables native files, Git views, and PTY at the backend, not merely in the browser; `--share-computer` explicitly selects the default. Only implemented surfaces are advertised as available. Bare terminal CLI and embedded App instances do not enable native sharing implicitly; embedding hosts must select it explicitly. Listener authentication follows [App access](../05-runtime-subagents-and-surfaces.md#http-startup-and-access). Authentication bypass does not imply computer-sharing enablement.

The authority is the server's OS user, not the Agent's current Sandbox policy. Project roots are useful navigation entry points, not a claim of filesystem confinement when a native terminal exposes the OS account. The UI makes that authority and location visible. Native access does not register additional Agent tools or bypass a Provider's operation policy.

Shared instance access is a trusted-team boundary, not per-participant filesystem isolation. Human actions through these surfaces are not represented as actions taken by the Agent.

## Host Files

The file surface supports directory browsing, text viewing/editing, upload/download, creation, rename, move, deletion, and deliberate selection of file content as prompt context. It works outside Git repositories. Project roots anchor normal navigation without requiring a fake repository or a live Run.

File content, names, paths, and errors are bounded detached values; the API exposes no file descriptors or native object references. Downloads and previews do not execute arbitrary content as workbench-origin scripts. Large or binary content has explicit limits and fallback presentation rather than silent truncation represented as a complete editable file.

Edits made through the browser, PTY, Agents, or external editors can affect the same files. External updates do not replace an unsaved browser buffer. A stale content save reports a conflict rather than knowingly overwriting a newer observed file revision. A failed or interrupted write is not reported as saved. General filesystem operations are not presented as a transaction across multiple files or arbitrary external writers.

### Native Paths and Observations

Requests use absolute native UTF-8 paths of at most 4096 characters, without shell interpolation or implicit home expansion. `/api/projects` supplies all configured navigation roots; callers can also navigate unrelated accessible paths without a Project or Run. Metadata describes the final directory entry without following a symlink and distinguishes regular files, directories, symlinks (including dangling links), and other native objects. Content reads and directory browsing follow symlinks and return the resolved path and target revision. Content saves reject final symlinks; saving their target requires explicitly selecting that resolved path and its revision. Rename and delete act on the final entry, not a symlink's target. Special objects can be inspected, renamed, or deliberately removed, but are not opened as regular file content.

`revision` is an opaque native metadata observation, incorporating file identity, mode, link count, size, and nanosecond modification/change times where available. Windows uses creation time in place of the path stat's legacy change-time field and excludes filename-inferred execute bits from revision comparison; the opened handle's change time is additionally checked across a read. It is neither an application counter nor a content-addressed history. Read snapshots check the opened regular file before and after reading and check its named entry again. A concurrent detected change returns a conflict, not mixed content represented as a reviewed snapshot. Filesystems that do not expose a change through this metadata cannot supply a stronger comparison guarantee.

A directory page contains bounded entry metadata, the resolved directory identity/revision, and an optional next offset. Entries are lexically ordered by native name. Pages contain at most 500 entries; directory scans stop at 10000 entries and fail explicitly rather than returning an apparently complete partial tree. Further pages require the observed directory revision. A detected membership change requires restarting at offset zero. Child content edits do not necessarily change their parent's revision; paged metadata is not one atomic tree snapshot.

### Content and Mutations

Editable content is complete, NUL-free UTF-8 up to 512 KiB. Other content has `binary` or `too_large` presentation with no editable text, never silent truncation. Raw upload, download, and whole-file selection are bounded to 10 MiB per file. Larger transfers fail explicitly and require another native workflow. Downloads are detached bytes with attachment disposition and octet-stream content type, not active workbench resources. Text preview is JSON data, not an HTML response.

Text save and raw upload use the same mutation boundary. An absent `expected_revision` means create only; an existing destination is a conflict. Replacement requires the observed regular-file revision. Complete bytes are written and flushed to a same-directory temporary file before the precondition is rechecked and publication occurs. New-file publication does not overwrite a concurrent destination; replacement is atomic at the file-entry boundary. Replacement preserves permission bits but does not promise preservation of extended attributes, ACLs, ownership, or hard-link identity. Saving a hard-linked file replaces only the selected directory entry; other aliases retain their original bytes. New files use private owner permissions; directory creation uses the server account's ordinary umask. A returned revision observes the published entry after temporary cleanup, not a reservation against later external edits.

Directory creation requires an existing parent. Rename/move accepts an observed source revision and an absent destination; it never intentionally replaces an observed destination or silently copies/deletes across filesystems. Cross-device failure leaves the caller to choose a separate transfer workflow. The native move primitive atomically refuses an existing destination, including one created concurrently. Unsupported native no-replace semantics fail explicitly rather than falling back to an overwriting rename. Another process can still race a source revision check or a content replacement precondition; these operations do not claim an OS-wide compare-and-swap transaction.

Deletion requires the observed entry revision. A nonrecursive directory deletion only removes an empty directory. Explicit recursive deletion first inspects at most 10000 entries and 128 directory levels without following directory symlinks, then checks observations before and during removal. Exceeding preflight bounds removes nothing. Later failures can leave a partially removed tree; the error reports the completed removal count and requires refresh. Directory revisions do not represent every descendant's content. No multi-file rollback or undelete is promised.

The App serializes its own native operations and runs blocking filesystem work off the event loop. Started worker I/O is not abandoned on coroutine cancellation; a disconnect does not undo a started mutation. Native filesystem stalls remain subject to the OS, not a promised application deadline. An interrupted, failed, or lost response may have an unknown mutation outcome; callers refresh the affected paths rather than automatically replaying writes, moves, or deletion. The App owns no mutation receipt database or durable retry queue.

### Captured Prompt Content

A file selection names an observed target revision and optionally an inclusive, one-based start/end line range. Line selection requires editable text and both valid endpoints. Selection captures actual bytes immediately, with `location: host`, the requested and resolved paths, revision, and selected range. It stages those bytes through the existing Thread attachment owner; the result is a Thread-scoped attachment plus optional attributed `prompt_text`. Whole-file binary or larger-text selections retain their bytes, not a placeholder claiming the content was read inline.

Captured NUL-free UTF-8 of at most 64 KiB has attributed inline text. Submitting its attachment ID adds that text and source metadata to the ordinary model input as well as retaining the captured file. Other captures remain ordinary retained attachments with their source and location; this does not make a remote Environment automatically contain the native source. Submit never rereads the mutable source path. Unsubmitted captures have the ordinary Thread scratch lifetime; submitted captures have the existing retained-input lifetime, with no new store or CRDT registry.

Root and child steering remain text-only. A caller can deliberately use the bounded attributed `prompt_text` when present; an attachment ID is not accepted as steering input. Binary or larger attachment selections remain in the caller's draft for ordinary submission and must not be silently discarded to make steering succeed. Shared-draft synchronization consumes this snapshot boundary rather than inventing another file-source authority.

Structured resource publication remains owned by [configuration](../01-configuration-and-resource-catalog.md#file-mutation-and-last-write-wins). Editing a configuration file through native Host Files is direct source editing: it does not bypass generation validation or make malformed content active. The accepted generation can remain unchanged while the source tree contains an invalid manual edit.

## Git-Aware Files and Changes

Git enriches native file navigation; it is not the filesystem authority or a second application-owned version-control database.

| Projection            | Meaning                                                                                                     |
| --------------------- | ----------------------------------------------------------------------------------------------------------- |
| Repository context    | Actual repository/worktree root, branch or detached HEAD, and relationship to the browsed Project directory |
| File status           | Modified, added, deleted, renamed, untracked, ignored, or conflicted state as reported by Git               |
| Unstaged changes      | Index versus working tree                                                                                   |
| Staged changes        | HEAD versus index                                                                                           |
| Selected diff context | Captured comparison and location chosen for the shared prompt                                               |

The file tree displays relevant status, supports changed-file navigation, and makes ignored-file filtering explicit. Ignored files are not removed or made inaccessible by a display filter. Changes separates staged and unstaged state; a file can belong to both. Untracked content is not misrepresented as a tracked-file diff.

Git determines repository boundaries and worktree metadata, including `.git` files. A Project can contain several roots or repositories, or be a subdirectory of one repository. Nested repositories and submodules retain their own identity rather than being flattened into a single Project-wide index. A missing Git executable, inaccessible repository, or failed status query leaves ordinary Files usable and is not rendered as proof of a clean tree.

Diff views distinguish text, binary, deletion, rename, conflict, and an unborn HEAD. Repository and file paths are visible so a local diff cannot be mistaken for remote E2B content. Selected lines and diffs can be included in the [shared draft](01-collaborative-conversations.md) as explicit reviewed context.

### Discovery, Status, and Comparisons

Discovery accepts an existing absolute native path and lets Git identify its worktree root, per-worktree Git directory, common Git directory, HEAD object ID and branch. A detached HEAD has no branch; an unborn branch has no HEAD object ID. Bare repositories and locations without a worktree have explicit non-worktree results, not empty change lists. A `.git` directory inside a non-bare repository is not itself a worktree. Git availability is independent of Files: the listener advertises Git only when sharing is enabled and an executable is discoverable; individual query failures remain explicit errors.

Status uses Git's index/worktree codes separately, retaining rename source paths, conflict state and submodule flags. Ignored entries are opt-in; ignored directories can be summarized by Git. Entries are lexically ordered by repository-relative UTF-8 path. Pages contain at most 500 entries, and a status scan is limited to 10000 entries. Further pages require the previous status revision. That opaque revision describes repository identity and status records, not every file's content: edits that leave the status codes unchanged need not change it.

A diff selects one literal repository-relative file or submodule path and one comparison: `staged` (observed HEAD versus index, or the empty tree for an unborn branch), `unstaged` (index versus worktree), or `untracked` (explicit new-file comparison). Directory-wide diffs and ignored-file comparisons are not selected-file previews; ordinary Files still provides their navigation and content. Rename previews retain the original path for the selected axis. Conflicts use Git's unmerged/combined presentation, not an invented resolved baseline. Binary/non-UTF-8 patches have explicit `binary` presentation without editable text. A tracked file with no changes on the selected axis has `unchanged` presentation.

A diff revision covers the repository/worktree identity, observed HEAD, selected and original paths, comparison axis, selected index entries and complete patch bytes. The index entries are checked across the query; a detected change fails with a conflict. Diff and capture can require an expected diff revision. This does not promise a transaction across arbitrary external writers or imply the worktree remains unchanged after the response.

### Captured Diff Context

Capture requires the reviewed diff revision and immediately recomputes that comparison. A mismatch returns a conflict before staging input. Text diffs can be captured whole or as an inclusive one-based range of patch lines, including headers when selected. Binary and unchanged previews cannot be captured as text; Files is the explicit route for binary file bytes. The captured bytes use the existing Thread attachment lifetime and submission path described under [Captured Prompt Content](#captured-prompt-content), including its 64 KiB inline-text bound and text-only steering boundary.

Git source metadata has `kind: git_diff`, `location: host`, repository and per-worktree Git directories, selected/original paths, comparison, observed HEAD, index revision, diff revision and optional patch-line range. Existing file-source metadata remains unchanged and readable. Submission uses the captured bytes and provenance, never a fresh Git query. Captures are not attributed to a Run's edits and do not create another diff history or source registry.

### Shared State and Refresh

Git state belongs to the actual checkout and index. All Threads and people using that checkout observe the same underlying state. Changes does not label the repository's dirty state as edits made by one Agent or Run; such attribution cannot be inferred from a shared worktree.

Queries compute fresh observations on demand; the App keeps no Git projection cache, watcher or repository database. Clients refresh after their own file mutations, on returning to Changes and through an explicit refresh action. Changes from native terminal commands, Agent tools, or other processes become visible on that bounded refresh. A status query and later diff may observe intervening changes and do not claim an atomic repository snapshot.

Each Git subprocess is bounded to 15 seconds, 2 MiB of standard output and 64 KiB of diagnostics. Exceeding a bound fails explicitly, not with a silently truncated patch. Cancellation terminates and reaps the owned process. Viewing a large repository does not require computing every diff or scanning it on every keystroke. Read-only previews disable external diff/text-conversion helpers, filesystem monitor hooks, optional index refresh writes and lazy object fetching. Ordinary repository content attributes still determine Git's comparison semantics. Git is invoked with unambiguous arguments and literal path selection, not shell interpolation of filenames; ambient Git targeting variables cannot retarget the selected path.

### Mutation Boundary

The Git UI provides status, reading, comparisons, and context selection. It does not automatically stage, commit, discard, switch branches, create worktrees, or perform remote Git operations. Users can deliberately perform their Git workflow in Host Terminal. Git awareness does not turn navigation into a repository mutation.

## Host Terminal

Host Terminal provides a real native PTY with interactive input, terminal resize, control characters, and supported interactive applications. An Agent Shell result panel is not a PTY substitute. If a Host cannot provide required native PTY semantics, the terminal is explicitly unavailable rather than silently replaced with noninteractive execution.

Terminal sessions are App-owned live resources associated with Project context, independent of any chat Run. Changing the selected Thread does not destroy a terminal or change its working location. Multiple terminal sessions may exist.

Multiple participants can observe one terminal. One participant supplies input at a time, and control transfer is explicit and visible; keystrokes from independent participants are not silently interleaved. Terminal size follows the input controller rather than competing viewers. Losing a viewer does not by itself terminate the process.

A browser disconnect closes its attachment, not the terminal. Reattachment displays available current output and discloses gaps; it neither replays keyboard input nor claims missing output has been recovered. Terminal close is an explicit action affecting the shared session. App shutdown closes its owned native terminal resources and child processes. Server restart does not restore a live PTY from conversation or draft state.

Finite output retention is inspection evidence, not a complete durable terminal log. If a write or control action has an uncertain outcome, the browser does not retry keystrokes or destructive actions automatically.

## Environment Development Boundary

Native files and terminal can be used to edit Capability, Plugin, Provider, or Host-adapter source, run tests and user-authored scripts, and inspect normal Agent execution. Python code loading follows the existing App lifetime contract, not an assumed live module replacement.

These uses do not require an Environment-specific file browser, terminal, debug command console, or transfer UI. Remote Provider credentials and execution remain behind the normal Agent integration boundary. Host repository state is never presented as an automatically synchronized copy of a remote sandbox.

## Invariants

1. Native human operations are enabled only by the instance's computer-sharing selection.
2. Host access and Agent Environment policy are distinct authorities and locations.
3. Browser navigation cannot silently retarget an existing terminal or switch a repository.
4. File conflicts preserve unsaved user content rather than silently discarding it.
5. Git views distinguish index, working tree, and repository identity and make no unsupported per-Run attribution.
6. Terminal observation does not grant simultaneous uncontrolled keyboard input.
7. Browser disconnect and server shutdown have different terminal lifecycle effects.
8. Neither Git refresh nor terminal reconnect replays user mutations.
