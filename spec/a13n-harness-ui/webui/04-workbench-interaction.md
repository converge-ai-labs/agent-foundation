# Workbench Interaction

## Design Position

This document owns the browser's default interaction flow, action placement, and feedback. [Overview](00-overview.md) owns the product boundary; [collaborative conversations](01-collaborative-conversations.md) and [Host computer sharing](02-host-computer-sharing.md) own state and operation semantics. Layout never creates another authority or changes the selected execution target.

The primary experience is doing work in a conversation. Configuration is available when needed, and code or a terminal can be opened alongside that work. The interface does not require users to understand resource inheritance or runtime internals before sending a prompt.

## Entry and Navigation

1. Open the instance URL and complete the existing key-entry flow if necessary.
2. Restore this browser's last accessible Project and Thread. A direct Thread link takes precedence. Missing or archived selections lead to a visible navigation choice, not a silently created replacement conversation.
3. An empty installation offers Add Project and access to existing setup/configuration. Projectless conversation remains an explicit option.
4. Add Project collects a name and server directory, with additional roots under an expandable control. It displays that paths belong to the server/container. Without native sharing, path entry still works through validated Project configuration and does not enable a file-browsing API.
5. New conversation uses the selected Project's effective creation defaults. Agent and Environment choices are visible before first submission; incomplete configuration links directly to the blocking setting.

The left sidebar groups root conversations under Projects. Each Project offers New conversation and Project settings. Conversation rows show title, activity, and attention-needed state. Running, waiting for a decision, and failed states remain distinguishable without opening every Thread. Rename/archive actions are secondary; archive honors the App's active-operation restriction.

Selecting a different conversation is personal navigation. The browser retains each Thread's editing context without cancelling execution or causing collaborators to switch views. An update in another Thread marks its row rather than stealing focus.

## Default Layout

| Region       | Default interaction                                                                        |
| ------------ | ------------------------------------------------------------------------------------------ |
| Sidebar      | Projects and conversations; collapsible                                                    |
| Main area    | Conversation, with the shared composer at its end                                          |
| Context pane | Closed until a file, diff, configuration detail, or execution detail is opened             |
| Bottom panel | Host Terminal, closed until explicitly opened                                              |
| Header       | Project/Thread identity, compact effective Agent/Environment information, and participants |

Files and Changes share the code context pane. Opening a file reveals it beside Chat on wide screens; file tabs support comparing several pieces of code without replacing the conversation. The terminal is independently resizable/collapsible. Closing a panel closes the view, not its server-side terminal session or Agent Run.

On narrow screens, these areas become switchable views rather than compressed simultaneous columns. Returning to Chat preserves the unsent draft and personal scroll. Desktop and narrow layouts expose the same operation outcomes and warnings.

## Conversation and Shared Composer

The transcript foregrounds prompts and Agent responses. Tool activity is grouped with concise status and expandable arguments/results. Child execution appears as inspectable subordinate activity, not an unrelated root conversation. Pending questions and approvals have actionable cards adjacent to the relevant execution.

The composer shows collaborator cursors, selected context, and editing status: local changes, synchronizing, or saved. Participants can change their display name without creating an account. Names/colors provide interaction context, not verified identity or private workspaces.

Enter inserts a newline. Ctrl+Enter or Cmd+Enter submits, as does the explicit Send button; IME composition never triggers submission. This avoids sending a jointly edited prompt while someone is adding a line. Submission requires the initiating browser's pending edits to synchronize; offline input remains editable but cannot start a Run.

While submission is being resolved, the initiating browser shows Submitting and suppresses repeated sends. On confirmed acceptance, all participants see the same submitted snapshot and execution. A normal completed cutover presents an empty next prompt when there are no uncaptured edits. Edits outside the submitted snapshot remain recoverable under the collaboration contract; the UI never claims success by blindly clearing the editor. Rejected input stays editable with its error. An uncertain result offers status reconciliation, not an unqualified Retry button that starts a second operation.

While the Agent runs, the composer is labeled Next message and remains collaboratively editable. Ordinary Send is unavailable until the Thread can accept another root prompt; editing does not create a queue or automatically submit when the Run finishes. An explicit Send as instruction action is available only when the current receipt supports steering and is clearly distinguished from a new turn. Both forms preserve the same shared-draft snapshot and uncaptured-edit guarantees. Stop is a separate control targeting the displayed current operation.

At a pending question or approval, the relevant response controls explain what needs an answer. After a participant resolves it, other views show the result and disable the stale action. A control conflict refreshes the request instead of presenting a second successful response.

Share conversation copies a same-instance link without a long-lived API key. Other participants use the existing access flow. Sharing a link does not grant access or create a new Thread.

## Project and Resource Configuration

Project settings has Roots, Agent, Environment, and Tools sections, plus an effective-configuration summary. Agent-owned Models and Capabilities are edited through their owning resource rather than duplicated as another Project schema. Compact conversation controls show current choices; advanced changes open configuration rather than filling the main composer with switches.

The global configuration center manages reusable resources. Project settings selects from them. A selector distinguishes available, configured, and unusable resources, and links failures to concrete diagnostics. Installed package presence is not rendered as readiness.

Each inherited field shows its effective value and source. Users see Use default, Custom selection, and, for a collection, None rather than needing to infer the difference between omitted and empty YAML. Lists follow the [whole-list replacement rules](../01-configuration-and-resource-catalog.md#global-defaults); the UI does not silently union Project and Agent plugins.

One Project has one default combination, not a nested preset library. Saving defaults states that they affect new conversations. Editing shared resource content separately explains that later Runs using that resource can change. Changes to Project roots retain their existing later-Run effect and are not described as defaults-only edits.

An existing Thread offers Apply Project defaults. It displays a before/after comparison and applies the selected Project's explicitly configured axes, leaving unspecified axes unchanged. It uses the Thread's expected configuration version; a competing update requires refresh and another review. During a Run the UI distinguishes the captured configuration from next-run selections. Applying settings never claims to reconfigure the running Agent.

Forms and advanced source editing share the resource validation boundary. Source diagnostics preserve the user's editing text. Resource publication follows its owning file-write contract; the UI does not promise an atomic multi-file transaction or a source-version conflict check that the publication API does not provide. Saving configuration and making it the accepted active generation are separately reported when necessary.

## Reading Code and Changes

When native sharing is enabled, Files opens at the Project roots. Quick file selection, path breadcrumbs, and line selection support reading code. Clicking a file reads it; editing and saving are explicit. Dirty tabs and externally changed content are visible. Leaving a dirty editor offers Save, Keep open, or Discard local edits; the latter is not a Git discard operation.

Changes groups staged, unstaged, and untracked content by repository. Selecting an entry opens the appropriate comparison, with a clear baseline and a route to the current file. Binary files and conflicts receive explicit presentation rather than misleading text diffs. Git-unavailable state retains ordinary Files and explains why Changes cannot be shown.

Add to prompt on a code selection or diff inserts a visible context item into the shared composer of the selected Thread. Participants can inspect and remove it before submission. It identifies the server/repository/path and captured content, and does not imply access to that path from a remote Agent Environment. Merely opening a file does not send it to the model.

Repository changes made by people, terminal commands, or Agents refresh the relevant status without changing the reader's selection or overwriting a dirty editor buffer. The view is labeled Changes, not This Run's changes. Git mutation buttons, automatic commits, and automatic worktree creation are outside this interaction flow; deliberate Git commands remain available through Terminal.

## Terminal Interaction

Open Terminal shows an existing selected session or an explicit New terminal action. Starting a session displays its server/container location and initial directory. Terminal tabs have useful titles and an active input-controller indicator. Collapsing the panel does not stop sessions.

A participant viewing another person's terminal sees read-only output and a Take control action. Activating it transfers input control through the App, visibly updates both participants, and only enables keyboard input after confirmation of ownership. Losing control leaves the old viewer attached to output. It does not inject partially typed browser input. Viewers may open separate terminal sessions instead of taking over.

Disconnect shows a disconnected state rather than an apparently usable prompt. Reattach restores available output and discloses gaps; it never repeats keystrokes. Close terminal is distinct from closing its view and warns that it affects the shared process. Unsupported native PTY remains explicitly unavailable.

Terminal focus receives its native key bindings; browser-wide command shortcuts do not intercept shell control sequences. File-save shortcuts operate only in the file editor, not the shared prompt or terminal.

## Empty, Disabled, and Failure States

| Situation                          | User-facing behavior                                                                                                                                      |
| ---------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Computer sharing disabled          | Hide active Files/Changes/Terminal controls and explain the startup option in instance information; no misleading Enable button without process authority |
| Missing/invalid model or extension | Link the blocked conversation to its configuration diagnostic; preserve its prompt                                                                        |
| API key rejected                   | Request a valid key while distinguishing access failure from lost project data; do not repeat mutations after login                                       |
| Browser offline                    | Mark presence/output as disconnected, preserve recoverable local work, disable execution submissions                                                      |
| Draft synchronization failed       | Show unsynchronized state and retain recoverable text; do not claim server persistence                                                                    |
| File changed externally            | Preserve the local buffer and offer comparison/reload rather than silent overwrite                                                                        |
| Repository inspection failed       | Explain unavailable Git state, not a clean checkout                                                                                                       |
| Run failed                         | Show the owning operation error and retained evidence; keep deliberate recovery separate from retrying unknown effects                                    |
| Server restarted                   | Restore continuation, disclose lost shared drafts and live authority, and never automatically resubmit input or recreate a terminal                       |

## Verifiable Flows

1. A person opens a configured Project, starts a conversation, and submits without entering the configuration center.
2. Two browsers coedit one prompt, observe one accepted submission, and retain any uncaptured edits.
3. One collaborator reads a file/diff while another stays in Chat; adding context is visible to both, navigation is not synchronized.
4. A busy Thread permits next-prompt editing but does not silently queue or send it.
5. Applying Project defaults previews a bounded change and leaves an active Run's capture unchanged.
6. Native Files and Terminal remain on the server when the Agent uses E2B.
7. Terminal takeover changes input authority without disconnecting other viewers or replaying input.
8. Disabled sharing, missing Git, unsupported PTY, and authentication failures have distinct recovery paths.
