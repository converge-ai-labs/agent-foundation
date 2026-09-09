# a13n Console

## Product Boundary

`a13n-console` is the browser application for a13n Service. It consumes the public Native API through the TypeScript SDK and uses the shared [design system](design-system.md). Service remains API-only; production ingress serves Console assets and proxies `/api` on the same origin. Development uses a Vite proxy rather than permissive Service CORS.

Console owns navigation, forms, resource cache, localized presentation, and interaction rendering. Service owns authentication, permissions, resource versions, execution, durable state and object storage. Closing a page, stream, or SDK client never interrupts a Run.

## Navigation and Scope

The sidebar contains Agents, Sessions, resource management (Models, Skills, Assets, Environments), integrations (Application Accounts, Connectors, MCP Connections), and Traces. Usage and Schedules are absent from the primary navigation until available; their existing deep links display a Coming soon view without API calls or simulated data. Console exposes no Plugin editor, Secret management, or Webhook subscription editor.

The Workspace picker occupies the top of the sidebar. Workspace settings occupy its footer. The User menu opens personal settings and authorized Organization settings, selects language, and signs out. OSS has one Organization and no Organization switcher. Settings replace the primary sidebar with scoped section navigation and a return action; primary and contextual sidebars are never shown together.

Personal settings contain profile, browser-local appearance and language preferences, email and password management, browser sessions and personal security activity. Workspace settings contain profile, members, invitations, Personal API Keys, Service Accounts and their keys, and security audit. Organization settings contain profile, members, invitations, Workspaces, shared configuration and security audit. Organization-shared configuration reuses the resource editor with an explicit scope.

A Personal API Key belongs to its User and is bounded to one Workspace. Its creation and list live in that Workspace's settings. Administrators can inspect member-key metadata and revoke keys but cannot create another User's Personal API Key or recover its bearer value. Service Account keys live under their owning Workspace Service Account. Newly created bearer values appear once and are not persisted by Console.

## Sessions and Runs

Sessions present Service Sessions directly. The Session list replaces the primary sidebar, with a return-to-workspace action at its top. Session links open a Thread and its current or head Run without intermediate selection pages. The detail uses a compact Thread selector, semantic Items, the composer, pending actions and queued submissions. Each Run is inspected inside that Session through a panel containing input/output, available configuration evidence, RunAttempts, lifecycle events and linked Trace details. Console provides no independent Run-list navigation. A Run deep link selects its Session, Thread and Run within the same layout.

Agent trial execution opens this same Session surface. Continue, historical Continue, Fork, Retry, Feedback, Steer, Interrupt and queue operations use the owning Native contracts and exact current version evidence. The UI distinguishes queue acceptance from Run acceptance and delivery receipt from command consumption. Waiting interactions expose their required feedback; Console never automatically executes arbitrary Client Tools.

Stream cursors advance only after events are applied. Replay gaps trigger current resource and Item reconciliation, with unavailable retained content shown explicitly. Notifications are wake-ups, not durable history. Trace absence is not execution success, and unavailable cost or usage is not zero.

## Resource Editing

Agent authoring places the selected Model, instructions, and attached capabilities in the main content, with save status and version actions in the page toolbar. Capability selection opens on demand; model settings and advanced configuration remain collapsed by default. Validation expands the relevant section and identifies the field. Collapsing preserves the draft. Editing one field preserves other configuration, including Plugin and Secret requirements that Console does not expose. Saving executable configuration creates a Revision; restoring historical content creates a new Revision rather than moving the head backward.

Provider types and model parameter schemas come from Service. A discovered candidate is not a saved Model. Skills retain immutable package revisions; Assets are immutable uploads without overwrite, rename, or revision actions. Environments distinguish configured Providers, template recipes, and actual instances. Integration authorization and credential ownership follow their Service contracts.

Mutations retain exact ETags or expected versions. A conflict keeps the draft and offers an explicit reload; Console does not silently overwrite newer data. Permission projections guide visible controls but never replace server authorization. Cache identity includes the authentication session and resource scope; sign-out clears sensitive state, and Workspace switching does not reuse another Workspace's resource data.

## Experience

English is default and fallback; Simplified Chinese is supported. Shared tokens, primitives, menus, settings compositions and keyboard interactions provide consistent presentation. Run inspection opens in a named dialog with focus restoration and a layout that fits narrow viewports. Lists and forms provide loading, empty, error, forbidden and stale-data states. The supplied a13n brand SVG is bundled locally.

Session transcripts keep a readable content width and follow live output while the reader remains near the bottom. Scrolling up preserves the reading position and exposes an explicit return-to-latest action. The composer remains available at the bottom, with attachments and next-run options in named dialogs. Cmd/Ctrl+Enter sends outside IME composition; ordinary Enter inserts a newline. Completed assistant messages offer copy feedback. Approval decisions are explicit per-action choices before submitting the complete response set.
