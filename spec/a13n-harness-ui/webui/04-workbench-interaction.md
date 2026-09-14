# Workbench Interaction

## Design Position

This document owns the browser's default interaction flow, action placement, and feedback. [Overview](00-overview.md) owns the product boundary; [collaborative conversations](01-collaborative-conversations.md), [output comments](05-output-comments.md), and [Host computer sharing](02-host-computer-sharing.md) own state and operation semantics. Layout never creates another authority or changes the selected execution target.

The primary experience is doing work in a conversation. Configuration is available when needed, and code or a terminal can be opened alongside that work. The interface does not require users to understand resource inheritance or runtime internals before sending a prompt.

The workbench uses the shared frontend design system with English interface text only. Controls describe user actions, such as Log in, Log out, Save changes, Connect account, and Reconnect, rather than internal credential-retention or publication operations. It has no language selector or translation runtime. Light and dark themes remain available; English-only interface text does not restrict the language of prompts, configuration content, names or comments.

## Entry and Navigation

1. Open the instance URL and complete the existing key-entry flow if necessary.
2. Restore this browser's last accessible Project and Thread. A direct Thread link takes precedence. Missing or archived selections lead to a visible navigation choice, not a silently created replacement conversation.
3. A fresh installation automatically opens the route-backed `/setup` wizard before last-conversation restoration. A configured installation keeps ordinary restoration; an incomplete or broken existing configuration offers focused repair rather than reinitialization. Set up later dismisses automatic entry for this installation, with Continue setup available explicitly. Projectless conversation remains an explicit option.
4. Add Project collects a name and server directory, with additional roots under an expandable control. It displays that paths belong to the server/container. Without native sharing, path entry still works through validated Project configuration and does not enable a file-browsing API.
5. New conversation uses the selected Project's effective creation defaults. Agent and Environment choices are visible before first submission; incomplete configuration links directly to the blocking setting.

The left sidebar groups root conversations under Projects. Each Project offers New conversation and Project settings. Conversation rows show title, activity, and attention-needed state. Running, waiting for a decision, and failed states remain distinguishable without opening every Thread. Each conversation row's secondary menu provides Rename, Share, Comments, and Details. These actions belong to that Thread, not to its Project's settings. Archive remains in conversation details and honors the App's active-operation restriction.

Selecting a different conversation is personal navigation. The browser retains each Thread's editing context without cancelling execution or causing collaborators to switch views. An update in another Thread marks its row rather than stealing focus.

## First-Use Wizard

The browser first-use flow follows [Setup and Environment Readiness](../06-setup-and-environment-readiness.md#browser-first-use). It uses a large, centered, viewport-bounded dialog over the subdued workbench on desktop and the full viewport on narrow screens. Three steps share one stable progress header, scrollable content region, and persistent Back/Continue/save footer: Connect, Choose a model, and Workspace. Step changes focus their heading; dismissal and first-conversation entry restore useful keyboard focus. The wizard uses the shared overlay and field primitives in both themes.

Subscription connection is inline and shares account operations with Accounts settings. Device authorization is the default; callback authorization is an explicit advanced option that explains server loopback reachability. The wizard presents verification URL, code/copy, waiting, success, cancellation, expiry, and retry without requiring a detour. API-key entry saves through the existing Host key API under a generated reference, with an explicit immediate-save explanation. Neither connection method treats browser identity as the owner of server credentials.

The Model step uses backend-owned release choices, reasoning/context defaults, and reviewed native-tool recommendations, with optional advanced controls. API connections expose provider, model ID, and applicable base URL instead of requiring raw route syntax. The Workspace step explains Full Control versus Sandbox, offers an optional existing server directory, and shows a concise summary with expandable generated files. Sandbox readiness is bound to every resolved preview path; failed or cancelled preparation never selects Full Control.

Save and start chatting explicitly publishes configuration, reconciles saved files, opens the first conversation once, and focuses its empty composer. It does not send a prompt or test the model. Connected account, saved configuration, prepared environment, and successful model request remain distinct observations. Unknown publication or conversation creation stays recoverable on refresh and never causes blind mutation replay.

## Participant Awareness

On first entry the browser generates a display name and remembers it as a local preference. The top-right header shows that name and opens editing; saving a nonempty replacement updates live presence and composer attribution, and supplies the author label for newly created comment drafts. Existing saved comments and private publication drafts keep their captured author labels. This is a collaboration label, not an account or verified identity. The workbench exposes a compact participant directory showing reported current page and foreground, inactive, or disconnected/unknown participation. The current page shows its own participants rather than everyone with that Thread's draft open. The directory remains usable in Project settings and the configuration center without opening a conversation.

Separate tabs with the same display name remain distinguishable. A background tab can be shown as attached but is not labeled as confirmed current attention. Switching between Chat, a file, a diff, configuration, and a terminal updates the reported focused area without moving other users. Participants can explicitly open a collaborator's available page through ordinary navigation; it does not enable continuous following or synchronize scroll.

Same-page presence is an awareness indicator, not a blanket Edit permission or a claim that every surface uses CRDT. Chat retains its shared composer, saved output offers comments, file changes use existing save rules, and terminal input uses explicit control ownership. SSE updates and refetches preserve the local focused area and do not recreate the editor's CRDT replica.

## Default Layout

| Region       | Default interaction                                                                                                        |
| ------------ | -------------------------------------------------------------------------------------------------------------------------- |
| Sidebar      | Projects and conversations, Overview, Settings, and footer participant/theme/session controls; a drawer on narrow screens  |
| Main area    | Chat remains the primary reading area with its shared composer                                                             |
| Right drawer | Files or Changes explorer and selected file/diff editor, closed until opened; file tabs stay inside the drawer             |
| Bottom panel | Resizable Host Terminal, closed until explicitly opened                                                                    |
| Header       | One compact conversation title with Files, Changes, and Terminal toggles plus the editable collaboration name at the right |

The application frame stays within the viewport. Conversation history, code, explorer contents, and Settings content scroll within their own available space rather than growing the document. Files and Changes share the right drawer; selecting a file or comparison opens its editor inside that drawer without replacing desktop Chat. Back returns to the explorer, and file tabs retain previously opened views. The drawer can be widened explicitly and closed without discarding private buffers; closing it returns focus to Chat. The header does not stack instance status, a global file-tab row, and a second conversation title. The terminal is independently resizable/collapsible. Closing a panel closes the view, not its server-side terminal session or Agent Run.

Settings uses a dedicated content layout. It does not display Chat/Files/Changes/Terminal controls or native panes. Entering Settings detaches the selected terminal view without closing its session and retains private file/configuration drafts. Returning to the workbench lets the user reopen existing tabs and explicitly reconnect to terminal output.

On narrow screens, the right drawer or terminal temporarily takes the available work area rather than compressing simultaneous columns. Chat remains mounted, and closing the panel reveals it. Returning to Chat preserves the unsent draft and personal scroll. Desktop and narrow layouts expose the same operation outcomes and warnings.

## Conversation and Shared Composer

The transcript foregrounds prompts and Agent responses. Adjacent exploration calls, shell operations, web browsing, and file modifications form default-collapsed activity summaries such as **Explored**, **Ran commands**, **Browsed the web**, and **File changes**. Each group contains at most 16 calls. Visible prose, input, other activity, and category changes end a group; grouping never reorders or deletes source parts. Expanding a group exposes each operation's semantic description and details without another per-call disclosure. Routine result-received labels stay quiet; pending, failed, denied, interrupted, retry, and terminal-without-result states remain visible even while collapsed.

Loaded saved calls and their following results are paired before grouping, across loaded entry boundaries. Provider-native and local function calls have separate identities. A result whose call is outside the loaded page remains inspectable. Native provider search snapshots update one correlated tool row, retain final arguments and provider identity, and use the same browsing summaries in live and saved views. Arguments completion does not establish execution completion.

A **File changes** group opens the observed before/after diff, including when a later tool result fails, and provides the existing Host file lookup alongside the operation. It is associated only by exact tool-call identity; proxy inner calls with independent identities remain separate observations. Root edit evidence retained under the [continuation storage contract](../03-local-storage-and-recovery.md#tool-presentation-evidence) survives the switch to saved history and reload. Omitted evidence is explicitly identified rather than reconstructed. Older historical replacement arguments are labeled as requested input, never represented as applied changes. Live large changes retain the existing bounded-preview fallback and inspectable recorded content.

An absolute path can offer an explicit **Open on host** lookup when native files are enabled. This human action looks up the same path spelling on the WebUI server/container; it does not map Environment mounts or assert that a remote file is the Host file. It opens current Host content or the existing private editor buffer, not recorded historical bytes. Relative paths and Environment aliases are not automatically resolved.

Cross-root collaboration tools have action-oriented summaries for discovery, creation, continuation, messages, and steering. A known target Thread is an inline same-instance link outside the disclosure trigger; it remains usable without expanding raw arguments, including when creation succeeded but Run admission failed. Discovery results expose loaded conversation and Project links and compact Agent/Model identities. Clicking a link is personal navigation, not a new submission or a change of execution target. Admission/steer acceptance is not rendered as completion or saved delivery.

Child execution appears as inspectable subordinate activity, not an unrelated root conversation. Pending questions and approvals have actionable cards adjacent to the relevant execution.

The composer shows collaborator cursors, selected context, and editing status: local changes, synchronizing, or synchronized in this server instance. Synchronization is not a durable save. Participants can change their display name without creating an account. Names/colors provide interaction context, not verified identity or private workspaces.

Enter inserts a newline. Ctrl+Enter or Cmd+Enter submits, as does the explicit Send button; IME composition never triggers submission. This avoids sending a jointly edited prompt while someone is adding a line. Submission requires the initiating browser's pending edits to synchronize; offline input remains editable but cannot start a Run.

While submission is being resolved, the initiating browser shows Submitting and suppresses repeated sends. On confirmed acceptance, all participants see the same submitted snapshot and execution. A normal completed cutover presents an empty next prompt when there are no uncaptured edits. Edits outside the submitted snapshot remain recoverable under the collaboration contract; the UI never claims success by blindly clearing the editor. Rejected input stays editable with its error. An uncertain result offers status reconciliation, not an unqualified Retry button that starts a second operation.

While the Agent runs, the composer is labeled Next message and remains collaboratively editable. Ordinary Send is unavailable until the Thread can accept another root prompt; editing does not create a queue or automatically submit when the Run finishes. An explicit Send as instruction action is available only when the current receipt supports steering and is clearly distinguished from a new turn. Both forms preserve the same shared-draft snapshot and uncaptured-edit guarantees. Stop is a separate control targeting the displayed current operation.

At a pending question or approval, the relevant response controls explain what needs an answer. After a participant resolves it, other views show the result and disable the stale action. A control conflict refreshes the request instead of presenting a second successful response.

Share conversation copies a same-instance link without a long-lived API key. Other participants use the existing access flow. Sharing a link does not grant access or create a new Thread.

## Comments on Saved AI Output

A saved assistant text block offers Comment; a supported text selection offers Comment on selection. Selection exposes a contextual action beside the text, opening a nearby private editor rather than requiring the Thread-wide discussion dialog. Supported saved selection ranges are highlighted in the original rendered text; pointer and keyboard activation opens their discussion. Overlapping highlights preserve each exact source range. Closing a discussion preserves an unpublished or uncertain draft. Original text remains available from the output's secondary actions for transformed or otherwise unrepresentable selections. Streaming or otherwise unsaved output does not offer successful publication: the UI explains that the output must first be saved, and never treats completion of token delivery as checkpoint success.

The sidebar's Thread menu opens its complete paginated comment list, showing published comments, author labels, creation times, and original output references. Inline counts describe loaded comments, not an unqueried total; target-specific browsing does not replace the all-output marker overview. Inline markers are attached only to an established saved target. A comment whose source is not in the current history view remains in that list with an explicit original-output view, rather than being moved to a similar-looking response. The comment list and referenced-output view remain useful after later Runs and compaction. Opening an old referenced output never selects that checkpoint for continuation.

Post comment acknowledges a committed comment, distinct from the shared composer's Synchronized state. Publication failure or a stale target preserves the local comment text. A lost acknowledgement offers reconciliation of the same publication identity, not a second post. Newly published comments update the discussion without replacing the transcript, stealing focus, or clearing another input. Reload and reconnect refetch saved comments independently of presence and draft rejoin.

Comments are human discussion by default. An explicit Add feedback to prompt action captures the complete selected comment and its original assistant output into the shared composer's context selection for review; it does not immediately call the model. The composer presents an inspectable comment reference, while model input carries the actual captured text and presentation metadata. Existing Send and explicit steering controls retain execution authority. No comment-read or navigation action changes the shared prompt implicitly.

## Project and Resource Configuration

Project settings has Roots, Agent, Environment, and Tools sections, plus an effective-configuration summary. Agent-owned Models and Capabilities are edited through their owning resource rather than duplicated as another Project schema. Compact conversation controls show current choices; advanced changes open configuration rather than filling the main composer with switches.

Settings groups General defaults, Agents & models, Capabilities, Environments, Projects, Accounts & API keys, MCP connections, and Advanced configuration. Setup and diagnostics remain accessible within Settings. Existing source and Project deep links remain usable.

Capabilities selects an owning Agent and edits its existing capability list; selection stages a draft, and Save changes persists the complete source. Discovery distinguishes configurable from unavailable implementations. Reusable Harness plugin instances remain distinct from Agent capabilities and scope selections; there is no global plugin Enable mutation. Environments shows configured profiles, read-only built-in environments, and installed provider discovery. Configuring a provider creates an environment profile, not installation or readiness. Provider-specific adapter/settings remain editable through the existing configuration file. Project settings edits its existing roots/defaults source directly. No second configuration or draft store is introduced.

Save changes includes validation; Check configuration is optional and does not save. New resource actions open a private editable draft directly, with unsaved drafts discoverable in their relevant lists. Installing server packages is outside browser configuration. Installed package presence is not rendered as readiness.

Each inherited field shows its effective value and source. Users see Use default, Custom selection, and, for a collection, None rather than needing to infer the difference between omitted and empty YAML. Lists follow the [whole-list replacement rules](../01-configuration-and-resource-catalog.md#global-defaults); the UI does not silently union Project and Agent plugins.

One Project has one default combination, not a nested preset library. Saving defaults states that they affect new conversations. Editing shared resource content separately explains that later Runs using that resource can change. Changes to Project roots retain their existing later-Run effect and are not described as defaults-only edits.

An existing Thread offers Apply Project defaults. It displays a before/after comparison and applies the selected Project's explicitly configured axes, leaving unspecified axes unchanged. It uses the Thread's expected configuration version; a competing update requires refresh and another review. During a Run the UI distinguishes the captured configuration from next-run selections. Applying settings never claims to reconfigure the running Agent.

Forms and advanced source editing share the resource validation boundary. Source diagnostics preserve the user's editing text. Resource publication follows its owning file-write contract; the UI does not promise an atomic multi-file transaction or a source-version conflict check that the publication API does not provide. Saving configuration and making it the accepted active generation are separately reported when necessary.

## Reading Code and Changes

When native sharing is enabled, Files and Changes follow the selected conversation's Project rather than a remembered server folder or the first configured Project. The first Project root is the default; additional configured roots are exposed as compact Project-folder navigation, not an arbitrary-path form. Without an explicit native link, a Projectless conversation or a Project without roots receives a clear empty state. Explicit file and repository links retain their requested location and explorer; Project defaults are navigation, not native access restrictions. File browsing remains local to the drawer; opening Changes inspects the selected Project root rather than the last browsed subdirectory. A named Up action and clickable ancestor breadcrumbs remain visible above both folder listings and file editors. The current folder is distinguished from its ancestors; Up stops at the active Project root, while explicit outside-Project locations retain their native ancestry. Back to files returns from an editor to its containing directory without dropping the private buffer or file tab. Quick file selection and line selection support reading code. Clicking a file reads it; editing and saving are explicit. Unsaved and externally changed content are visible in the editor. Switching or closing a file view retains its private buffer; it does not save or discard it. Reloading or leaving the browser warns about unsaved file content. Explicitly choosing the inspected disk version discards local text, not Git changes.

Changes groups staged, unstaged, and untracked content by repository. Selecting an entry opens a unified comparison in the right drawer, with a clear baseline and a route to the current file. Text uses monospace code, separate old/new file gutters, and distinct light/dark additions, deletions, and hunk context. The reviewed patch remains complete: headers and no-newline notices stay readable, while unknown/combined metadata receives no invented file numbers. Selection and capture retain the original inclusive patch-line coordinates, not the displayed file gutters; a replacement diff revision clears the previous selection. Binary files and conflicts receive explicit presentation rather than misleading text diffs. Git-unavailable state retains ordinary Files and explains why Changes cannot be shown.

Add to prompt on a code selection or diff inserts a visible context item into the shared composer of the selected Thread. Participants can inspect and remove it before submission. It identifies the server/repository/path and captured content, and does not imply access to that path from a remote Agent Environment. Merely opening a file does not send it to the model.

Repository changes made by people, terminal commands, or Agents refresh the relevant status without changing the reader's selection or overwriting a dirty editor buffer. The view is labeled Changes, not This Run's changes. Git mutation buttons, automatic commits, and automatic worktree creation are outside this interaction flow; deliberate Git commands remain available through Terminal.

## Terminal Interaction

Open Terminal lists only sessions associated with the selected conversation's Project. New terminal immediately starts in that Project's selected root and carries its Project ID; there is no separate folder or Project chooser. Without a Project and configured root, creation is unavailable. Switching Projects changes the visible session list and detaches the old view without closing its sessions, sending commands, changing their working directories, or claiming control. A creation completing after navigation cannot select its old-Project session in the new Project. Existing unassociated sessions remain available through the native API, not through another Project's list. Terminal links require the matching Project context; unavailable targets explain that requirement and do not publish terminal page focus. Starting a session displays its server/container location and initial directory. Terminal tabs have useful titles and an active input-controller indicator. Collapsing the panel does not stop sessions.

The creating browser requests control once on the new terminal's first authenticated frame when it has no controller, then focuses input only after server confirmation. It does not take over from a participant who already acquired control, and reconnecting never repeats this creation intent. A participant viewing another person's terminal sees read-only output and a Take control or Take over input action. Activating it transfers input control through the App, visibly updates both participants, and only enables keyboard input after confirmation of ownership. Losing control leaves the old viewer attached to output. It does not inject partially typed browser input. Viewers may open separate terminal sessions instead of taking over.

Disconnect shows a disconnected state rather than an apparently usable prompt. Reconnect restores available output and discloses gaps; it never repeats keystrokes. End session is distinct from closing its view and warns that it affects the shared process. Unsupported native PTY remains explicitly unavailable.

Terminal focus receives its native key bindings; browser-wide command shortcuts do not intercept shell control sequences. File-save shortcuts operate only in the file editor, not the shared prompt or terminal.

## Empty, Disabled, and Failure States

| Situation                                     | User-facing behavior                                                                                                                                      |
| --------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Computer sharing disabled                     | Hide active Files/Changes/Terminal controls and explain the startup option in instance information; no misleading Enable button without process authority |
| Missing/invalid model or extension            | Link the blocked conversation to its configuration diagnostic; preserve its prompt                                                                        |
| API key rejected                              | Request a valid key while distinguishing access failure from lost project data; do not repeat mutations after login                                       |
| Browser offline                               | Mark presence/output as disconnected, preserve recoverable local work, disable execution submissions                                                      |
| Draft synchronization failed                  | Show unsynchronized state and retain recoverable text; do not claim server persistence                                                                    |
| File changed externally                       | Preserve the local buffer and offer comparison/reload rather than silent overwrite                                                                        |
| Repository inspection failed                  | Explain unavailable Git state, not a clean checkout                                                                                                       |
| Run failed                                    | Show the owning operation error and retained evidence; keep deliberate recovery separate from retrying unknown effects                                    |
| Server restarted                              | Reload continuation and committed comments, disclose lost shared drafts and live authority, and never automatically resubmit input or recreate a terminal |
| Comment source or selected target unavailable | Preserve published comments or the local unpublished draft, explain the unavailable/stale target, and never silently attach to different output           |

## Verifiable Flows

01. A person opens a configured Project, starts a conversation, and submits without entering the configuration center.
02. Two browsers coedit one prompt, observe one accepted submission, and retain any uncaptured edits.
03. One collaborator reads a file/diff while another stays in Chat; adding context is visible to both, navigation is not synchronized.
04. A busy Thread permits next-prompt editing but does not silently queue or send it.
05. Applying Project defaults previews a bounded change and leaves an active Run's capture unchanged.
06. Native Files and Terminal remain on the server when the Agent uses E2B.
07. Terminal takeover changes input authority without disconnecting other viewers or replaying input.
08. Disabled sharing, missing Git, unsupported PTY, and authentication failures have distinct recovery paths.
09. Two people can see whether they are on the same Chat, file, or configuration page without being navigated there by each other; multiple/background tabs are distinguishable.
10. A participant comments on saved output while another Run executes; the comment survives reconnect, restart, and a later continuation without entering model history.
11. Streaming output cannot receive a published comment, and selecting a rendered range never silently anchors to different source text.
12. A lost comment acknowledgement is reconciled without a duplicate post; a missed live hint is repaired by an ordinary comment query.
