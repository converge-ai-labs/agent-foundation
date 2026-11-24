# Workbench Interaction

## Design Position

This document owns the browser's default interaction flow, action placement, and feedback. [Overview](00-overview.md) owns the product boundary; [collaborative conversations](01-collaborative-conversations.md) and [Host computer sharing](02-host-computer-sharing.md) own state and operation semantics; [Harness WebUI design guidelines](06-design-guidelines.md) own visual presentation.

The UI calls a human-facing conversation a **Chat** across navigation, actions, search, settings, sharing, notifications, and accessibility labels, while Thread and protocol IDs keep their existing meanings. Controls use direct user-action labels (Log in, Log out, Save changes, Connect account, Reconnect).

### Localization

Harness WebUI owns its i18next runtime and catalogs; shared UI receives application-owned strings. All application-owned chrome—login/setup, navigation, Projects, Inbox, composer, Model/Thinking/Speed/Reasoning controls, transcript disclosures, usage analytics, sharing, files, terminals, Browser, voice, and settings—provides English, Simplified Chinese (`简体中文`), and Hindi (`हिन्दी`, keeping product terms Agent, Run, Thread, Session, and Skill in English).

- **Preference:** Viewers choose System, English, 简体中文, or हिन्दी at login/setup or in Preferences (browser-local, independent of server config and Console). System maps `en*` → English, `zh*` → Simplified Chinese, `hi*` → Hindi, falling back to English.
- **Live switching:** Switching updates UI text and document `lang` immediately without reloading, refetching, mutating config, or losing drafts, undo history, attachments, open panels/dialogs, Browser leases, or pending/uncertain receipts. Embedded MCP Apps receive the resolved locale via Host-context updates without remounting.
- **Verbatim values:** User/model content, authored titles, Project/resource names, identifiers, paths, commands, logs, currency codes, recorded amounts, and unknown backend/provider diagnostics stay verbatim.

## Appearance

Settings → Preferences → Appearance controls browser-local theme (Light default, Dark, or System), independent terminal theme (Follow theme default, Light, or Dark), and six integer font sizes applied before initial render across login, owner, and guest views:

| Region       | Default | Range   | Content                                                          |
| ------------ | ------- | ------- | ---------------------------------------------------------------- |
| Navigation   | 13px    | 12–16px | Projects, chats and settings navigation                          |
| Interface    | 14px    | 12–16px | Menus, forms, buttons, tool labels and metadata                  |
| Conversation | 15px    | 14–20px | User and assistant messages, thoughts, Markdown lists and tables |
| Composer     | 16px    | 14–20px | Message input text                                               |
| Code         | 12px    | 11–18px | Code blocks, raw JSON, logs, source editors and diffs            |
| Terminal     | 13px    | 11–20px | Terminal text and cell grid                                      |

Changes apply immediately in 1px steps with a static preview, per-region resets, and **Reset appearance** (restoring all defaults and Light theme while leaving language, credentials, sidebar width, and drafts intact). The browser stores `{version: 1, theme, terminalTheme, sizes: {...}}` under `a13n-harness-ui.appearance` (migrating legacy `a13n-harness-ui.theme` only when the new key is absent; invalid/unknown data falls back to defaults without overwriting; storage failures keep in-memory state with an inline note). Same-origin tabs apply storage updates without writing back. Appearance changes preserve editor state, undo history, terminal connections (remeasuring PTY only if controller), and conversation bottom-follow or visible message-block anchor.

## Coordinator Entry

- **Promotion and composer opt-in:** Ordinary project-bound roots (not active, archived, or pending-decision) offer one-way **Make Coordinator** in actions/details with confirmation. In the composer's **Chat options**, **Coordinate agents** (Coordinator) and **Goal** are independent choices; Coordinator stages private intent (requiring an available Project and promotion eligibility, locked during pending/unknown/preparing sends; never silently falling back to ordinary execution). New conversations create directly as Coordinators; existing roots promote before submission with separate completion boundaries. Confirmed Coordinators show a non-toggle identity.
- **Owned workers:** A Coordinator's **New worker** opens the new-chat composer with its Project and a removable **Managed by - name** chip (locking Project selection until removed or created; permanent once created; unavailable/archived/mismatched owners block creation).
- **Navigation and controls:** Coordinators form tree nodes with a leading worker disclosure (collapsed by default, lazy-fetched with `coordinator_thread_id`), medium-weight title, trailing active-worker count, and **Pause/Enable automatic follow-up** in their own actions. A Coordinator appears once among active rows while either it or any unarchived worker is active (progress spinner reflects only the Coordinator's own execution), returning to **New results** if either has unread results. Workers never appear as top-level recent/active rows; missing or archived owners stay anchored with Restore; search marks workers **Coordinator worker**.

## Delete Chat

Instance owners can choose **Delete chat** on ordinary root rows (Project lists, Archived, or chat details; disabled while active; absent on Live, Memory, and child conversations). Confirmation names the chat, explains permanent removal of its subagent history while retaining Project files and other chats, and submits the displayed metadata version once (disabling repeat submit/dismiss while pending; errors stay in dialog). Success clears local drafts and caches, refreshes navigation, and replaces a deleted selected chat with New chat in its Project ([deletion scope](../03-local-storage-and-recovery.md#permanent-thread-deletion)).

## Usage Analytics

The sidebar footer workspace menu opens **Usage** (`/usage`), also reachable from Project menus (**View usage**) and Thread usage details (**Open analytics**) with matching scope. URL state owns filters (default 30 UTC days, 7/30/90-day presets, custom up to 366 days; intersecting Project, searchable root Thread, and selected-model filters), grouping, ordering, and pagination.

The page shows estimated USD spend, total tokens (with input/output subtotals), model requests, a daily tokens/cost chart (pointer and keyboard inspectable), and a paginated Project/Thread/model breakdown with an expandable **Token details** toggle (input, output, cache, unknown-cost) and **About these estimates**. Compact K/M/B units expose full counts and precise values in tooltips/labels; unknown prices stay distinct from zero. Reads run on entry, filter change, and explicit Refresh, disclosing read timestamp and observation coverage ([consumption analytics](../03-local-storage-and-recovery.md#consumption-analytics)).

## Entry and Navigation

- **Display modes and sidebar:** The sidebar heading places a **Conversation display** menu (**Projects** default, or **Conversations** → **Recent** cross-Project list ordered by navigation touch time + Thread-ID tie-breaker) beside its plus action. **New**, **Search**, and **Inbox** lead the sidebar above Projects; the footer menu lists Settings, Usage, and Log out; Archived chats and Live open from Settings → Chats. Desktop sidebar resizes 200–360px (default 220px, keeping ≥480px for content; double-click resets) and collapses locally; Settings replaces chat navigation in the same sidebar/drawer.
- **New chat and setup:** Fresh installations open `/setup`; configured installations open New chat resuming the browser's single local-storage draft (text, attachment refs, creation ID, selections, unresolved creation/submission state; local file bytes stay tab-local so reload marks them unavailable for removal/reattachment). First Send creates the Thread with its retained ID, uploads files, syncs the draft, and submits once (locking creation choices until exact-identity reconciliation on uncertain outcomes; absence uses `thread_missing`).
- **Model, Thinking, Speed, Reasoning, and Environments:** The composer right-aligns the Model + thinking trigger opening the **Agent & Model** popover (bottom sheet on mobile) with Agent, Model (searchable), Thinking (stepped track + Model default via [control descriptor](../02-agent-composition-and-snapshots.md#model-aware-thinking-controls)), Speed (Model default, Standard, Fast, Ultrafast via [Fast descriptor](../02-agent-composition-and-snapshots.md#fast-request-controls)), and Reasoning mode (Default, Standard, Pro via [descriptor](../02-agent-composition-and-snapshots.md#reasoning-mode-controls)). Changing Agent or Model clears dependent overrides; invalid choices stay explicit until reset; Send captures overrides atomically (steering excludes them); Debug shows captured active-Run settings separately. **Chat options** groups Goal, Coordinate agents, Environments (following [Device and Environment binding selection](../04a-devices-and-environment-bindings.md#webui-behavior)), and eligible **Clear context**.
- **Search and shortcuts:** `Cmd+F` / `Ctrl+F` opens chat search from ordinary workbench content (editors, terminals, Files/Changes/Browser keep local find; arrow keys navigate, Enter opens, Escape restores focus and resets query). Search runs one paginated global server query; `/archived` searches and restores archived roots independently.
- **Groups, drag reordering, and Inbox:** Each expanded Project group shows all active roots, then all unread roots (**New results**), then up to 5 read-history rows (stars first, **Show more/less**). Groups load via `include_active=true&include_starred=true` with navigation touch sorting. Project headings preview paths/counts on hover/focus, support quick rename, and reorder by drag or `Alt+Space` + arrows (default order puts Projects matching server cwd first). Ordinary roots reorder within their priority group (`Alt+Up/Down` or drag) and drag onto another available Project to transfer association (Coordinators/workers excluded). **Inbox** (`/inbox`) lists items newest first under **Open** (**Needs you** then **Updates**) and **Done** (Done implies read; `post_to_inbox` renders as a compact Inbox card in the transcript; **Open in chat** jumps via `?inbox=<item_id>` to the posting turn; **Settings → Chats → Inbox** toggles [`inbox.enabled`](../01-configuration-and-resource-catalog.md#inbox)). Thread rows show a quiet trailing pencil mark for [unsent shared input](01-collaborative-conversations.md#unsent-input-discovery). Row menus expose Rename, Share, Details, Archive/Restore (direct archive without confirmation).

### Shared Conversation Stars

Independent roots and Coordinators offer **Star/Unstar conversation** in their row menu (starred rows show a quiet filled mark noting stars are shared). Activation never navigates or confirms; errors stay inline without silent retry. Within read history, all starred rows load and precede unstarred rows in touch-time order. Application confirmations use styled dialogs rather than native browser dialogs.

## Shared Unread Conversations

Read/unread state belongs to the trusted instance and is shared across owner browsers for all human-navigable roots (including Coordinator workers under owner disclosures and visible Bot text publications; guests stay read-only).

- **State and mutations:** SQLite stores one boolean and monotonic `read_state_version` per Thread (constant storage per conversation). A successful ordinary root completion sets unread atomically with [saved root completions](../03-local-storage-and-recovery.md#saved-root-completions); Bot sets unread and advances `read_state_version` on each visible assistant publication. Row menus offer **Mark unread** (advancing `read_state_version` to fence stale read reports).
- **Automatic read and Mark all read:** Automatic clearing requires the saved transcript rendered in a visible, focused document scrolled to the bottom, sending exact `(completion_version, read_state_version)`. **New results** headers and group menus offer **Mark all read** (batches up to 100 non-archived unread Threads, capturing both versions at activation).

## Feedback Placement

App-wide connection, configuration, and result-tracking notices share one compact top banner; global disconnection suppresses duplicate network notices in dependent views. Missing/inaccessible Threads show one unavailable state with Retry and New chat. In available conversations, live connection/replay handshakes stay quiet for 1.5s before showing status in the Activity/usage row. Actionable recovery feedback (read failures, draft sync, rejected sends, uncertain outcomes, stop recovery) has one home directly above the composer, retaining explicit recovery controls until resolved.

## Task Notifications

- **Audience and scope:** Browser-local audience selects **Entire workspace** (default) or **My runs only** (matching a random origin-scoped browser profile ID attached at submission). Browser-local scope selects **All** (default), **Bot only**, or **Inbox only**, applying to both in-app notices and Web Push.
- **In-app notices:** Consumed from App-wide summary realtime; suppressed in-app when the route already views that conversation (still entering deduplication). Bot publications show **New message from Bot**; suspended Bot ops show **Bot needs your response**; Inbox items show **New in Inbox** (Needs-you stays until dismissed); Goal runs emit `goal_verified` or `goal_incomplete`.
- **Web Push:** System notifications use one path: server Web Push → notification-only Service Worker (`/sw.js`). Opt-in requires an explicit Settings action (iOS/iPadOS 16.4+ tabs explain Add to Home Screen). Active pages report activity immediately on startup/visible return/setup and every ~60s; recipient selection targets subscriptions with `last_active_at` within the last 6 hours. Endpoints: `GET /api/push/configuration` (public VAPID key), `PUT /api/push/subscription`, `DELETE /api/push/subscriptions/{subscription_id}`, `POST /api/push/subscriptions/{subscription_id}/activity`, and `POST /api/push/subscriptions/{subscription_id}/test`. Server uses a bounded in-memory queue, encrypted Web Push, persisted VAPID identity, no redirects, and up to 2 attempts for transient failures ([local storage](../03-local-storage-and-recovery.md#browser-push-subscriptions)).

## First-Use Wizard

Follows [Setup and Environment Readiness](../06-setup-and-environment-readiness.md#browser-first-use) in a centered dialog (full viewport on narrow screens) with two steps—**Model** and **Workspace**—sharing a progress header and Back/Continue/Save footer. Subscription login runs inline (Device auth default; callback auth optional), and API keys save via the Host key API under a generated reference. Workspace explains Full Control vs. Sandbox, accepts an optional server directory, and previews generated files (failed Sandbox preparation never falls back to Full Control). **Save and start chatting** publishes config, opens the first conversation, and focuses the empty composer.

## Participant Awareness

Browsers generate a local display name editable from the top-right header (collaboration attribution only; never rewrites saved attribution). The participant directory shows current page and foreground/inactive/disconnected state per tab (keeping duplicate names distinct). Opening a collaborator's page is an explicit personal navigation action.

## Default Layout

| Region       | Default interaction                                                                                                       |
| ------------ | ------------------------------------------------------------------------------------------------------------------------- |
| Sidebar      | New chat, search, Inbox, Projects and chats, supporting links, and footer workspace menu; a narrow-screen drawer          |
| Main area    | Chat with its shared composer, or the selected full-width view in Tabs layout                                             |
| Right drawer | Supporting pane with user-opened resource tabs and a New tab chooser in Side by side layout                               |
| View strip   | One resource-tab strip with a New tab action; Chat joins it in Tabs layout; Environment status appears only in Debug mode |
| Header       | Project and chat title, an icon-only supporting-panel toggle, participant awareness, Share, and chat actions              |

The frame stays within the viewport with independent scrolling per region. Files, Changes, Browser, and Terminal share one supporting pane and resource-tab strip (with keyboard-accessible split separator, per-Thread open/closed preference defaulting closed, and browser-wide split width/layout). Narrow screens display the supporting pane full-width over mounted Chat. Settings uses a dedicated layout, detaching terminal views without closing server sessions and preserving drafts. Browser document title follows `<title> - Harness UI`.

## Conversation and Shared Composer

- **Title generation and voice dictation:** New chats asynchronously generate a title on the first admitted human submission (budget: 2,000 words sampled from both ends; uses `chat_title_model` or chat Model; manual rename wins). **Rename chat → Generate title** previews an editable title. Instance-key users can dictate into the draft or **Add to chat** comment via Codex subscription dictation or an audio-input Model ([Dictation](../01-configuration-and-resource-catalog.md#dictation)); dictation inserts final text at the caret without auto-submitting.
- **Reply annotations and Clear context:** Selecting assistant prose offers **Add to chat** with an optional comment (persisted per tab/Thread across reloads; submitted with Send/Steer). Saved chats expose **Clear context** in **Chat options** (confirming selected continuation via [explicit context-clearing command](../05-runtime-subagents-and-surfaces.md#detached-surface-contracts); disabled while active/preparing/unknown).
- **Observation, history, and input rail:** The workbench pins live observations for all active roots plus a bounded recent warm set over one multiplexed realtime transport. Initial entry shows one loading surface until detail + first history page arrive. A left-edge input rail (popup on narrow screens) previews and jumps to ordinary inputs (512-char closing output preview), loading target history windows directly.
- **Execution stretches, tools, and questions:** Visible inputs and assistant prose stay in source order; completed reasoning, tools, notifications, and context summaries collapse behind **Worked** / **Worked for …** (desktop inline reader capped at `min(256px, 42.67vh)`; mobile full-screen modal). Adjacent calls (up to 16) group under **Explored**, **Ran commands**, **Browsed the web**, **File changes**, **Tasks & notes**, **Subagents**, and **Tool executions**, exposing formatted/raw **Input & result** in ordinary mode (Debug adds IDs/envelopes). Reasoning collapses under **Thinking** / **Thought** (with `for Ns` when recorded). Questions (`ask_user_question`) render inline outside execution details and correlate by native tool-call ID. **File changes** renders before/after diffs from [tool presentation evidence](../03-local-storage-and-recovery.md#tool-presentation-evidence) with **Open on host** lookup when native files are enabled. Source-marked background notifications use [Activity](#activity) disclosures.
- **Message actions:** Submitted inputs and saved closing replies offer Copy (preserving Markdown and `[filename]` placeholders) and local timestamps on hover/focus; eligible replies offer **Branch in new chat**; editable plain-text user messages edit inline to branch into a new chat.
- **Composer controls, shortcuts, and Skill completion:** Full-width editor above one action row (`Enter` submits, `Shift+Enter` newline, `Cmd/Ctrl+Enter` submits; `Option+Left/Right` on macOS / `Ctrl+Left/Right` elsewhere uses Chinese word segmentation in CJK text). Typing `$` or `/` opens Skill completion inserting `$name ` (rendered as a brand-colored unit; validated per [interactive skill references](../02b-environment-skill-sources.md#interactive-skill-references)). One bottom-right primary action switches between Send (idle + input), Steer (active + input), and Stop (active + empty; targets captured receipt, never auto-retried). Confirmed failed roots offer **Retry** submitting `Continue completing the previous task.` as a new turn. Pending decisions remain compatible with Send/Continue (sending denies unanswered requests).
- **Status strip and usage popovers:** One row above the composer shows Activity (left) and context tokens (`k` unit + occupancy ring + source breakdown when available) and combined model USD cost (right; opens cumulative usage popover with All / This chat / Subagents scopes). Bot shows this row in Debug mode only.
- **Scrolling and cutover:** Readers follow live output immediately while at bottom (upward scroll pauses following; top scroll loads earlier pages). Checkpoints suppress only the matching saved live prefix after history loads; Handoff/Compaction render as collapsible Summary disclosures.

### Composer input predictions

When enabled, the WebUI Host runs one background attempt via the [Harness post-run hook](../../a13n-harness/05-plugin-system.md#post-run-hooks-and-forked-inference) after an eligible root Run completes and releases its slot (no native tools; excludes Bot, memory, child, observation-only, and provider-managed stateful requests; usage attributed to `input_prediction`). Published `{continuation_id, text}` is stored in shared composer options and read via `POST /api/threads/{thread_id}/input-prediction`. The WebUI shows ghost text only in an idle, runnable, empty synchronized composer (Tab or accept button inserts as one undoable edit; Escape dismisses; Enter never accepts).

### Activity

**Activity** beside **Share** in the chat header shows running count and toggles a floating card (Subagents and Tool calls, up to 3 rows each, plus **View all** opening `?native=subagents` / `?native=tools` tabs in the supporting strip; Tasks and Notes also preview up to 3 rows). Subagents show one row per child conversation (latest segment, deterministic glyph, Stop on hover/focus); Tool calls include `foreground_released=true` executions and read-only Shell observations. Activating a row or inline **Moved to background** opens a detail resource tab (read-only transcript/output, header Stop for children/tools). Chat details separates **Activity**, **Configuration**, and **Tasks & usage** tabs.

## Saved Output

Saved output is read-only. Markdown renders GFM tables, code blocks with copy, static Mermaid previews (plain flowcharts, sequence, v2 state diagrams; complex/invalid stay source), and local KaTeX math (`$…$`, `\(…\)`, `$$…$$`, `\[…\]`, `math` fences; escape literal `$` as `\$`). Up to 8 same-instance native file links/images per message resolve near the viewport using authenticated [media preview](02-host-computer-sharing.md#content-and-mutations); external URLs stay plain links.

## Project and Resource Configuration

- **Settings structure:** Grouped into **Preferences** (Preferences, People & profile), **Workspace** (Chats, Agents, Bot, Models, Memory, Environments, Connections), and **Advanced** (Configuration files, Diagnostics). Single-value settings use horizontal name/value rows; long-form instructions use full-width editors.
- **Accounts, API keys, and Web providers:** **Accounts & API keys** separates Subscription accounts (grouped by provider with **Add account** for Codex, Grok, GitHub Copilot, ChatGPT; allowances/vouchers/credits separated; vouchers ordered by earliest expiry), Account groups (nested detail view), and API keys (grouped by referencing providers; secret values never read back). **Connections** includes Web provider accounts (search/scrape capabilities, pasted keys stored under references), MCP servers (explicit **Load/Refresh tools**, digest-checked **All tools** vs. **Selected tools**), Harness plugin instances, and [Connectors](../13-connectors.md) (shared dialog/bottom-sheet setup).
- **Models and Agents:** Models share one two-tab editor (**Connection** + **Model defaults** including `settings.openai_transport` WebSocket checkbox and preserved YAML comments). Agents group General, Tools & capabilities (including tool-search switch and per-MCP membership), Delegation (Agent children, Markdown children, instance roles), and Advanced, supporting inline Model creation.
- **Environments and Projects:** **Environments** groups Local execution, Devices, Profiles & extensions, Local runtime (`/settings/environments?tab=runtime`, [deferred local runtime setup](../06-setup-and-environment-readiness.md#deferred-local-runtime-setup)), and Browser (`/settings/environments?tab=browser`, [Host browser](../11-browser.md)). Project settings edits Roots, Agent, Environment, and Tools directly; **Apply Project defaults** / **Apply Project environments** previews before/after diffs and updates an existing Thread under its expected version. Inherited fields display Use default, Custom selection, or None ([whole-list replacement](../01-configuration-and-resource-catalog.md#global-defaults)).

## Reading Code and Changes

- **Resource tabs and layouts:** Supporting pane holds one strip of user-opened tabs (New tab chooser for Files, Changes, Browser, Terminal; at most one Terminal and one Browser tab per workspace; private file buffers survive tab close). Dragging the split separator to the left switches to Tabs layout; dragging right collapses the pane. Narrow windows default to Tabs; phones show one view at a time.
- **Files:** Follows the selected chat's Project roots (first root default; breadcrumb folder menu switches roots; inline directory expansion; Reveal in Finder/Explorer when local; **Back to files** restores folder/filter/focus). Renders code with syntax highlighting, wrap, find, and go-to-line; Markdown opens in Preview; PDF/PPTX/spreadsheets/SVG render locally; unsupported types offer download. Moves require inspected revision and never overwrite existing entries.
- **Changes:** Groups staged, unstaged, and untracked files by repository with readable labels, optional ignored filter, and unified diffs (patch-line coordinates for capture; binary/conflicts explicit; **Add to prompt** captures reviewed selection into the shared draft). Refresh observes current checkout state; Git mutations run in Terminal.

## Terminal Interaction

Terminal lists sessions for the selected Project (``` Ctrl+\`` toggles/selects Terminal,  ```Ctrl+Shift+\``creates a session in the browsed directory,`Ctrl+PageUp/PageDown`or`Cmd+Shift+[/]`switches sessions). Creating browsers claim control once on first frame; reattaching views reclaim only if they held confirmed control before an unexpected drop and no controller exists. Other viewers see read-only output with **Take control**.`Ctrl+F`/`Cmd+F`searches retained output;`Ctrl+Shift+C/V`(Win/Linux) or`Cmd+C/V\` (macOS) copies/pastes when controller; **Add selection to prompt** appends attributed text to the draft without submitting; **End session** confirms before terminating the shared PTY.

## Empty, Disabled, and Failure States

| Situation                          | User-facing behavior                                                                                                                            |
| ---------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------- |
| Computer sharing disabled          | Hide active Files/Changes/Terminal controls and explain the startup option in instance information                                              |
| Missing/invalid model or extension | Link the blocked conversation to its configuration diagnostic; preserve its prompt                                                              |
| API key rejected                   | Request a valid key while distinguishing access failure from lost project data; never repeat mutations after login                              |
| Browser offline                    | Mark presence/output as disconnected, preserve recoverable local work, disable execution submissions                                            |
| Draft synchronization failed       | Show unsynchronized state and retain recoverable text                                                                                           |
| File changed externally            | Preserve the local buffer and offer comparison/reload                                                                                           |
| Repository inspection failed       | Explain unavailable Git state explicitly                                                                                                        |
| Run failed                         | Show the owning operation error and retained evidence; keep deliberate recovery separate from retrying unknown effects                          |
| Server restarted                   | Restore unexpired composer drafts and continuation, disclose lost live authority, and never automatically resubmit input or recreate a terminal |

## Verifiable Flows

01. A person opens a configured Project, starts a conversation, and submits without entering the configuration center.
02. Two browsers coedit one prompt, observe one accepted submission, and retain any uncaptured edits.
03. One collaborator reads a file/diff while another stays in Chat; adding context is visible to both while navigation stays personal.
04. A busy Thread permits next-prompt editing without queueing or sending it automatically.
05. Applying Project defaults previews a bounded change and leaves an active Run's capture unchanged.
06. Native Files and Terminal remain on the server when the Agent uses E2B.
07. Terminal takeover changes input authority without disconnecting other viewers or replaying input.
08. Disabled sharing, missing Git, unsupported PTY, and authentication failures have distinct recovery paths.
09. Two people can see whether they are on the same Chat, file, or configuration page without synchronized navigation; multiple/background tabs stay distinguishable.
10. An Agent posts a final result to Inbox; the person sees the unread count, opens the item, lands on the posting card in the exact turn, and marks it Done without opening other chats.

## Debug Mode

Debug mode is a per-browser presentation preference, off by default, toggled from Diagnostics or `Cmd+Option+D` (`Ctrl+Alt+D` elsewhere) across owner and guest views. When enabled, surfaces reveal copyable identifiers, raw payloads, events, and token figures already held by the page without changing behavior or authority.

## Host Browser Drawer

The [Host Browser](../11-browser.md) shares the supporting panel with Files and Changes (resizable right panel in Side by side; full width in Tabs). It displays the remote browser desktop, defaults to read-only, and offers explicit **Take control** and **Hand back to Agent** actions; closing its tab detaches viewing without ending the Host browser or Agent Run.
