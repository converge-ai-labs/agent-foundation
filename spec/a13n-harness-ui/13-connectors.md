# WebUI Connectors

## Design Position

A Connector is an instance-wide, trusted Python integration owned by a WebUI-mode `HarnessUiApp`. It combines a discoverable declaration, human configuration and setup, private account state, optional native tools and shared programmatic services, and optional external input production. Multiple instances of one type represent independent accounts or endpoints.

This plane belongs to Harness UI, separately from Harness Plugins, Environment connectors, file-defined Agent MCP selections, and declarative Content Plugins. [Bot and Connector Ingress](12-bot-and-connector-ingress.md) owns automatic delivery and Bot publication; [Agent Composition](02-agent-composition-and-snapshots.md) owns immutable Run composition; Harness owns native Capability, ModelContext and ToolSearch behavior. Connectors add no Agent loop or general workflow engine.

## Discovery and Authority

The WebUI catalog combines built-in definitions, explicit `HarnessUiIntegrations.connectors` registrations and installed `a13n_harness_ui.connectors` entry points. An entry point constructs a no-argument `Definition` whose key matches its entry-point name. Declarations perform no network or credential I/O. Installed trusted types are available by default; installation creates no account, setup session, usable tool binding or Run. Duplicate keys are unavailable with a diagnostic rather than silently selecting an implementation. Broken installed definitions do not hide other types. Capture records retain implementation provenance; reconstruction fails explicitly if the captured implementation is missing or changed.

The owning WebUI App holds the same data-root local ownership lock as Bot. The lock spans producers, admitted root users, controller resources and cleanup. Another App can inspect saved facts but cannot configure, set up, submit input or use services. Terminal Apps do not construct this catalog or runtime. Automatic tools and context attach only to ordinary WebUI roots and Bot: children, Live and memory organization receive none. Bot still inherits no configured Agent instructions, children, MCP selections or Harness Plugins.

Python integrations are trusted Host code, with Host-account effects. The SDK's typed surface is a maintainability and ownership boundary, not a Python sandbox. Instance-key users administer all connections. Scoped guests have no Connector management, credential, setup-session or offer-query API authority. A shared intentional card is inert for guests.

## Declaration, Configuration and Rules

A declaration has API version `1`, stable type key, display name, description, configuration form schema, setup intents, optional event declarations and optional Bot delivery. Each event has a key, display metadata and optional conditions schema. The Definition supplies Pydantic configuration and event-condition validators; conditions schemas agree with those validators. Types may declare conditions, event-only rules, or no events at all.

An instance has a never-reused ID, type key, editable name, enabled flag, default-false `deliver_to_bot`, human configuration version, independent private credential version, settings and ordered rules. A rule has a stable ID, declared event key, enabled flag and conditions value. The Host validates and persists these values and renders their forms. The plugin decides what matches and what work to perform; the Host has no universal matcher, condition language, provider scope or event operator registry. Historical `selected_events` remains decodable but grants no new filtering meaning.

Human saves compare the expected configuration version; creation expects absence. Secret-changing saves also compare the private credential version. Refresh/rotation can change credentials independently without invalidating an open human form or a Run's captured configuration. Deleting clears private values, state and pending input, retires the instance ID permanently and prevents old captures from resolving a replacement account. Unknown plugin data is preserved as unavailable rather than reinterpreted as another type.

Credentials and private state are plaintext in the instance database under Host filesystem protection. Public instance reads expose safe settings, rule configuration and configured credential names, never credential values. Model context, captures, tool schemas, logs and durable setup offers contain no credentials or private state. Plugins own the safety of their status, context, setup and capture projections.

## Controller and Service Lifetime

A Definition opens one Controller per configured instance. Its async context enters and exits in one owning task, permitting ordinary async resources and task groups. The Controller remains available through root cleanup even if configuration disables or deletes the instance. Optional producers run for one configuration generation and stop before replacement. Shutdown seals admission and stops producers and setup, drains root users, then closes controller resources and relinquishes ownership.

The bound SDK Context exposes current private configuration, credential CAS, scoped JSON state, atomic state/input commits, configuration saves and typed shared-service lookup. It exposes no SQL session, Host Agent builder, root executor or raw HTTP adapter. Services use an explicit name and API version plus a consumer-checked type. They share a provider implementation and its current account authority rather than exporting token getters to consumers. Disabled or retired account checks remain at the provider operation boundary.

## Setup Interactions

Settings and conversation cards use the same backend-owned setup flow. Beginning or reading setup starts no Run. A session owns an opaque ID, instance, intent, version, expiry and current Step. Steps describe title, explanation, safe values, optional form, optional bounded wait and actions: submit, explicit confirmation, open URL or copy. State is `input`, `working`, `waiting`, `completed`, `failed`, `cancelled` or `expired`.

The bounded form profile supports objects, strings/passwords, numbers, integers, booleans, enums, nullable fields, repeatable arrays/rows and acyclic local `$defs` references. Conditional schemas, arbitrary unions, remote/recursive references, tuple schemas and open dictionary editors are unsupported. Unsupported declarations produce diagnostics; the UI does not substitute a raw JSON editor. Nested `writeOnly` values are removed from safe reads and cannot publish nonempty defaults. Private inputs exist only in component/request memory and are cleared on dispatch, without Query mutation caches or browser persistence.

Actions serialize per session and compare the observed version and action availability. Each request supplies an operation ID; an exact retry returns the retained safe result without repeating the action. A session retains at most 128 action outcomes and lives at most 30 minutes; the App retains at most 128 sessions. Individual actions have a 30-second deadline. Cancellation/shutdown can interrupt an action and closes the flow after that action exits. Interrupted or unknown external effects require inspection and a new deliberate decision, never automatic replay. Restart loses ephemeral sessions; saved configuration and credentials remain.

Wait reads are observational, bounded by the backend poll interval and deadline; a changed observed step changes the version. Expiry is explicit. Closing a panel discards its private draft, not saved configuration or an already dispatched effect. Reopening reuses an active session; a terminal flow begins afresh. After an unknown response the UI refreshes status before enabling another action. Configuration conflicts preserve human drafts and require an explicit reload.

## Captured Tools and Model Context

Root admission captures enabled, usable instances before publishing immutable composition. Each capture records instance/type identity, configuration version, implementation provenance and a plugin-authored safe binding recipe. Later status/setup changes do not add tools to that Run. Disabled, deleted or configuration-changed instances reject subsequent provider operations; a fresh deliberate Run can capture the new binding. Credential bytes resolve only at the provider transport boundary, independently of configuration capture.

Connector tools use native Capabilities and scoped ToolSearch with `discover_connector_tools`, `describe_connector_tool`, `call_connector_tool`, `list_connector_tools` and `list_connectors`. These controls coexist with Agent-owned ToolSearch. Real-target allowlists apply before discovery, including an explicit empty list. Each account has a stable distinct search group, so native tools with identical upstream names remain unambiguous without inventing wrapper arguments or a per-tool provider facade. Model-visible group descriptions identify the instance.

A changed-only ModelContext block describes current available types, safe instance status and context, captured membership, setup entry point and authority limits. It uses native visible-history matching, so A/A emits once, A/B/A emits each transition and loss through compaction permits reinjection. Context is an observation, not new tool authority.

`offer_connector_setup` accepts a type, optional same-type instance and human-facing text, never credentials. It writes a durable ordinary-Thread offer independently of folded CodeAct/tool output. The card appears outside collapsed work, beside its visible native-call turn when available and independently when its anchor is absent or compacted. Offering alone creates no connection or setup session. A human selects an existing account or deliberately creates another and enters the shared setup surface. Setup success changes saved configuration; it replays no blocked operation.

For Bot, the same native tool intentionally publishes a typed setup offer using the existing Bot publication identity, deduplication and notification path. Its chat remains human messages, final replies and intentional publications, without tool-stream rendering. Ordinary final-answer and `finish_silently` semantics remain unchanged.

## Private State and Bot Ingress

Scoped state consists of bounded JSON documents with independent CAS versions. A plugin persists its own codec/version inside each value when needed. Deletion retains a state-version tombstone to prevent stale update acceptance after delete/recreate. No dynamic per-instance SQL table is created.

One short transaction atomically validates the enabled instance/configuration generation, compares state versions, changes state and retains plugin-decided Bot inputs. It admits at most 32 distinct state changes, 64 KiB per JSON document, 256 KiB total changed state and 32 inputs per commit. At most 1024 pending envelopes belong to one instance. Every fully serialized input fits the shared 60 KiB automatic batch boundary. A failed fence, CAS, size, capacity or delivery-policy check aborts the entire commit. Ignored upstream events can advance state without input; selected input cannot be lost while advancing a cursor.

Pending inputs deduplicate by instance and source-event identity and return the originally retained input ID. Deduplication is not a permanent provider event log. Provider I/O happens outside database transactions. Submission signals Bot immediately through a coalesced process-local wakeup; periodic delivery remains compensation. Bot off, pause, pending human decisions, capacity and native admission retain their existing gates. Rule edits never erase previously retained plugin decisions; disabling holds them, deletion clears them.

## Remote MCP and GitHub

The built-in Remote MCP Connector wraps Streamable HTTP through the existing MCP transport validation, FastMCP client, native MCP Capability and ToolSearch lifecycle. It supports endpoint, private header rows, native tool allowlist and initialization timeout. Unset tool selection allows all advertised tools; `[]` allows none. Removing a header row clears it; an omitted/blank header value retains that header's saved credential. Plain HTTP follows the existing header-free loopback restriction. Header/token control characters are rejected before save.

GitHub is a preset of this generic Connector using `https://api.githubcopilot.com/mcp/` and a private PAT-derived Authorization header. The plugin declares no events or proactive Bot delivery. It implements no separate GitHub OAuth flow, REST tool library or polling source. Upstream token type, organization policy and permissions determine usable tools. Saving is local configuration, not verified connectivity; an explicit saved-connection test uses the same native MCP client.

Remote MCP publishes version `1` of the typed `mcp` shared service for programmatic list/call operations. It resolves current credentials internally and applies enabled/configuration/tool-selection checks. Native Agent tools remain native rather than delegating through this service.

## API and Compatibility

Instance-authenticated routes expose catalog and instance reads, conditional PUT/delete, begin setup, observational session reads, explicit actions and ordinary-Thread setup offers. The generated OpenAPI document owns exact payloads. These routes are absent from scoped guest admission. No Connector management YAML is added to Agent resources.

An additive SQLite migration supplies human and credential versions, identity retirement, rules, scoped state and durable ordinary offers. Existing unrelated resources, Bot behavior and checkpoints retain their semantics. Captured bindings use ordinary immutable composition persistence, including reconstruction and deferred execution; missing provenance or unsupported legacy settings fail unavailable rather than silently receiving new tools.
