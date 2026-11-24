# Collaborative Web Workbench

## Design Position

WebUI combines project-organized Agent conversations, configuration composition, and optional native computer access in one browser workbench. One person or a trusted small team uses one running Harness UI instance. Instance-key participants have full workbench authority; [sharing links](05-scoped-sharing.md) grant temporary view or prompt access to a Project or Thread. Prompt guests use the same Agent execution authority. There are no tenants, per-participant execution backends, or account-based resource isolation.

The server owns one process-local `HarnessUiApp`. The browser calls its public commands and consumes detached projections and live events. It never constructs Providers, reads SQLite directly, reconstructs Harness continuation from a transcript, or owns an Agent loop. The server remains active when all browsers disconnect.

[Workbench interaction](04-workbench-interaction.md) owns the default screen flow, action placement, keyboard behavior, and user-facing recovery states. These are browser behaviors over the domain boundaries below.

## Working Areas

| Area                       | Observable behavior                                                                                                         |
| -------------------------- | --------------------------------------------------------------------------------------------------------------------------- |
| Projects and conversations | Organize root Threads by Project, create and select conversations, and show current activity and requests needing attention |
| Conversation               | Show user input, Agent responses, tool activity, child execution, approvals, questions, and execution outcomes              |
| Context                    | Inspect relevant content, captured configuration, and execution details without replacing the conversation                  |
| Configuration center       | Configure reusable resources and compose them for work in a Project                                                         |
| Host Files and Changes     | Browse and edit server files and inspect the server checkout's Git state when computer sharing is enabled                   |
| Host Terminal              | Operate a native terminal on that same server when computer sharing is enabled                                              |

The default work path enters a Project and continues a conversation; execution detail stays inspectable without making raw logs the primary view. A saved browser conversation maps to an existing root Thread, while the [blank entry page](04-workbench-interaction.md#entry-and-navigation) holds local input before first Send. Child Threads stay subordinate inspection context, and projectless Threads keep their existing semantics.

Different Threads run concurrently under App admission rules; switching or closing a view leaves execution running. Project grouping and multiple conversations share the configured local paths without implicit worktree or filesystem isolation.

## Configuration Composition

The configuration center edits the [file-backed resource catalog](../01-configuration-and-resource-catalog.md) directly, exposing reusable Models, Agents, Capabilities, Harness Plugins, Environment profiles, Run Extensions, MCP servers, and content management.

A Project view combines working roots and new-conversation creation defaults (existing Threads keep their sticky selections until explicitly updated via a versioned action; each Run freezes its effective config at admission). Editing a shared resource affects later Runs selecting it, while a Project root edit follows its [later-Run effect](../04-projects-threads-and-environments.md#projects).

Forms group choices by Agent behavior, Environment, and tools/integrations without merging runtime ownership of Capabilities, Plugins, Providers, and Run Extensions. Discovery reports availability; only enabled Providers with a usable Host adapter appear ready for Project execution. Form and YAML edits share the same validation and accepted-generation boundary; already-imported extension code stays fixed for the App lifetime.

## Collaboration and Local View State

[Collaborative conversations](01-collaborative-conversations.md) owns page/focus awareness, editor presence, shared drafts, and concurrent control behavior. A single participant uses the same conversation model as a group.

Selected Project/Thread, scroll position, open files, and inspection panels remain personal view state. Participants report their currently focused semantic page and can see who shares it; this is awareness, not synchronized navigation. They do not move one another's focus merely by navigating. Shared changes, such as prompt edits, configuration publication, or a terminal control transfer, are explicit application operations.

The current shared composer is saved under the [bounded draft storage contract](../03-local-storage-and-recovery.md#shared-browser-drafts); presence remains in memory. Multiplexed execution observation and interactive collaboration have independent state owners and completion boundaries. Updating or reconnecting one does not reset the others.

## Authentication in the Browser

[Listener access](../05-runtime-subagents-and-surfaces.md#http-startup-and-access) owns the instance key and bypass behavior. The browser accepts a pasted key or reads the generated startup URL's `#api_key=...` fragment, removes that fragment from the address bar, and validates access before displaying application data. A successfully validated key is retained in same-origin `localStorage`; the browser offers an explicit forget-key action.

Startup validates access without briefly displaying the key-entry form, presenting a redundant initial connection screen, or mounting application-data consumers. Successful validation opens the original deep link. An unavailable listener offers connection retry with the same credential rather than claiming authentication failed; explicit server bypass remains supported without a key. A hard reload still reacquires server data and observation connections.

Missing or rejected credentials return the browser to key entry without treating authentication failure as missing Projects or lost Threads. First entry without a key and explicit logout show quiet login guidance; a rejected key attempt shows an inline error, while loss of previously validated access identifies the expired session. A new key attempt is evaluated as a new login, and empty input cannot be submitted. Reauthentication does not automatically repeat a mutation. Generated server-key rotation invalidates previously saved browser keys. Forgetting a browser key removes that browser's retained access and closes its authenticated connections; it does not revoke the shared key for other participants or cancel server execution.

HTTP uses authorization headers rather than request-URL keys. Interactive connections authenticate before subscribing to shared content or performing operations. Static shell delivery does not expose the key or application data. Arbitrary file content is not executed as workbench-origin HTML, because same-origin scripts can access browser credentials.

## Environment Boundary

[Host computer sharing](02-host-computer-sharing.md) provides remote access to the WebUI server machine (or container and its mounted directories), independent of any Agent Environment selection such as E2B. Developers use native Host files and PTY to edit code, run tests, and inspect files on the server; remote Agent Environments remain behind their own boundaries.

## Invariants

1. All collaborators operate through one App and one instance access boundary.
2. Conversation identity and execution authority remain owned by existing Threads and root operations.
3. The configuration center and direct file editing converge on the same desired-resource authority.
4. Changing views has no execution or shared-navigation side effect.
5. Effective configuration and location are visible without assuming local/remote filesystem equivalence.
6. Key validation, page presence, draft synchronization, operation admission, and Run completion are distinct facts.
7. Neither native computer sharing nor trusted collaboration introduces multi-tenancy or distributed recovery.
