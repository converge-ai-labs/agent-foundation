# Native Host Computer Sharing

## Design Position

Host Files, Git-aware Changes, and Host Terminal let people operate the machine running WebUI without a separate SSH connection. They support code reading, editing, testing, and ordinary development. The App owns these human-facing native operations independently of Agent Runs and Environment Providers.

Host means the server OS and its user account. When the server runs in the Harness UI Docker image, Host means the container and explicitly mounted directories. It never means the browser's machine. Selecting a remote Agent Environment does not retarget these surfaces.

## Enablement and Authority

`a13n-harness-ui webui --share-computer` enables native files, Git views, and PTY for that instance. Without this option the corresponding backend operations are unavailable, not merely hidden. Listener authentication follows [App access](../05-runtime-subagents-and-surfaces.md#http-startup-and-access). Authentication bypass does not imply computer-sharing enablement.

The authority is the server's OS user, not the Agent's current Sandbox policy. Project roots are useful navigation entry points, not a claim of filesystem confinement when a native terminal exposes the OS account. The UI makes that authority and location visible. Native access does not register additional Agent tools or bypass a Provider's operation policy.

Shared instance access is a trusted-team boundary, not per-participant filesystem isolation. Human actions through these surfaces are not represented as actions taken by the Agent.

## Host Files

The file surface supports directory browsing, text viewing/editing, upload/download, creation, rename, move, deletion, and deliberate selection of file content as prompt context. It works outside Git repositories. Project roots anchor normal navigation without requiring a fake repository or a live Run.

File content, names, paths, and errors are bounded detached values; the API exposes no file descriptors or native object references. Downloads and previews do not execute arbitrary content as workbench-origin scripts. Large or binary content has explicit limits and fallback presentation rather than silent truncation represented as a complete editable file.

Edits made through the browser, PTY, Agents, or external editors can affect the same files. External updates do not replace an unsaved browser buffer. A stale content save reports a conflict rather than knowingly overwriting a newer observed file revision. A failed or interrupted write is not reported as saved. General filesystem operations are not presented as a transaction across multiple files or arbitrary external writers.

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

### Shared State and Refresh

Git state belongs to the actual checkout and index. All Threads and people using that checkout observe the same underlying state. Changes does not label the repository's dirty state as edits made by one Agent or Run; such attribution cannot be inferred from a shared worktree.

After relevant browser mutations, projections are invalidated. Changes from native terminal commands, Agent tools, or other processes become visible through bounded refresh/observation, with an explicit refresh action available. Notifications are invalidation evidence, not complete repository history. A status query and later diff may observe intervening changes and do not claim an atomic repository snapshot.

Git operations and diff output are bounded; viewing a large repository does not require computing every diff or scanning it on every keystroke. Read-only previews do not execute external diff or text-conversion helpers merely to render content. Git is invoked with unambiguous arguments and literal path selection, not shell interpolation of filenames.

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
