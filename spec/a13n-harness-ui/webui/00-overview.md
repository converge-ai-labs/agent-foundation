# Collaborative Web Workbench

## Design Position

WebUI combines project-organized Agent conversations, configuration composition, and optional native computer access in one browser workbench. One person or a trusted small team uses one running Harness UI instance. All participants use the same instance authority; there are no tenants, per-participant execution backends, or account-based resource isolation.

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

The default work path is to enter a Project and continue a conversation, not to navigate through an administrative dashboard. Execution detail is inspectable without making raw logs the primary conversation view. Panel arrangement does not change command ownership.

A saved browser conversation is an existing root Thread, not a new Session identity. The [blank entry page](04-workbench-interaction.md#entry-and-navigation) holds local input before first Send and does not fabricate persisted Thread state. Child Threads remain subordinate execution/inspection context. Projectless Threads retain their existing semantics and are not assigned fabricated roots merely for navigation.

Different Threads can execute concurrently under App admission rules. Switching or closing a view does not cancel execution. Shared local paths can be modified by multiple Threads or people; Project grouping and multiple conversations do not imply worktree or filesystem isolation. The workbench shows the actual working location and does not create a worktree implicitly.

## Configuration Composition

The configuration center is an editor and selector over the [file-backed resource catalog](../01-configuration-and-resource-catalog.md), not a second definition store. It exposes reusable Models, Agents, Capabilities, configured Harness Plugins, Environment profiles, Environment Run Extensions, MCP servers, and supported content management through their owning contracts.

A Project configuration view combines working roots and the resource selections used to initialize new conversations. Users can inspect the effective combination and its source before execution. Existing Threads retain their exact sticky selections; editing Project creation defaults does not silently reconfigure them. Applying a Project's current selections to an existing Thread is an explicit versioned configuration action. Each Run freezes its effective configuration at admission.

The UI distinguishes changing a default resource selection from editing that resource's content. A shared resource edit can affect later Runs of Threads already selecting it. A Project root edit retains its separate [later-Run effect](../04-projects-threads-and-environments.md#projects). Neither operation changes an admitted Run.

Forms group choices by Agent behavior, execution Environment, and tools/integrations. These presentation groups do not merge the runtime ownership of Capabilities, Harness Plugins, Providers, and Run Extensions. Discovery reports availability; it does not enable an implementation. A discovered Provider without a usable Host adapter is not presented as ready for Project execution.

Common configuration is approachable without editing YAML; advanced source editing remains available. Both forms use the same validation and accepted-generation boundary. Invalid saves or unavailable dependencies have explicit diagnostics and do not silently select substitutes. Already imported extension code is fixed for the App lifetime; editing source is not universal hot reload.

## Collaboration and Local View State

[Collaborative conversations](01-collaborative-conversations.md) owns page/focus awareness, editor presence, shared drafts, and concurrent control behavior. A single participant uses the same conversation model as a group.

Selected Project/Thread, scroll position, open files, and inspection panels remain personal view state. Participants report their currently focused semantic page and can see who shares it; this is awareness, not synchronized navigation. They do not move one another's focus merely by navigating. Shared changes, such as prompt edits, configuration publication, or a terminal control transfer, are explicit application operations.

Presence and prompt CRDT state remain in memory. Multiplexed execution observation and interactive collaboration have independent state owners and completion boundaries. Updating or reconnecting one does not reset the others.

## Authentication in the Browser

[Listener access](../05-runtime-subagents-and-surfaces.md#http-startup-and-access) owns the instance key and bypass behavior. The browser accepts a pasted key or reads the generated startup URL's `#api_key=...` fragment, removes that fragment from the address bar, and validates access before displaying application data. A successfully validated key is retained in same-origin `localStorage`; the browser offers an explicit forget-key action.

Startup validates access behind a public, theme-consistent workbench shell without briefly displaying the key-entry form or mounting application-data consumers. Successful validation opens the original deep link. An unavailable listener offers connection retry with the same credential rather than claiming authentication failed; explicit server bypass remains supported without a key. A hard reload still reacquires server data and observation connections.

Missing or rejected credentials return the browser to key entry without treating authentication failure as missing Projects or lost Threads. Reauthentication does not automatically repeat a mutation. Generated server-key rotation invalidates previously saved browser keys. Forgetting a browser key removes that browser's retained access and closes its authenticated connections; it does not revoke the shared key for other participants or cancel server execution.

HTTP uses authorization headers rather than request-URL keys. Interactive connections authenticate before subscribing to shared content or performing operations. Static shell delivery does not expose the key or application data. Arbitrary file content is not executed as workbench-origin HTML, because same-origin scripts can access browser credentials.

## Environment Boundary

[Host computer sharing](02-host-computer-sharing.md) is remote access to the WebUI server machine, including when that machine is a container. It is not a browser surface over the Agent Environment and does not change location when an Agent selects E2B or another Provider.

There is no Environment-specific file browser, debug terminal, or command console. Environment configuration and Agent execution remain available through their existing application boundaries. Developers can use native Host files and PTY to edit extension code, run tests or scripts, and inspect code without a separate SSH login. A Host path or Git diff never implies that a remote Agent has the same checkout.

## Invariants

1. All collaborators operate through one App and one instance access boundary.
2. Conversation identity and execution authority remain owned by existing Threads and root operations.
3. The configuration center and direct file editing converge on the same desired-resource authority.
4. Changing views has no execution or shared-navigation side effect.
5. Effective configuration and location are visible without assuming local/remote filesystem equivalence.
6. Key validation, page presence, draft synchronization, operation admission, and Run completion are distinct facts.
7. Neither native computer sharing nor trusted collaboration introduces multi-tenancy or distributed recovery.
