# External Connectivity

## Design Position

The Connectivity subsystem lets Foundation-managed Agents receive authenticated external events and use authorized external tools without forcing provider payloads, credentials, or action schemas into one universal model. It owns the stable boundary between provider-specific Ingress adapters, Foundation Run acceptance, external integration services, user-configured Remote MCP endpoints, and the Agent-facing tool surface.

Connectivity has three distinct access paths:

- an `Ingress` receives authenticated provider events and can expose a small same-identity native action set;
- a `ConnectorProvider` discovers available `Connector` integrations and supplies general outbound SaaS tools through safe `ConnectorConnection` references while retaining third-party credentials outside Foundation; and
- an `MCPConnection` lets Foundation act as an OAuth-capable MCP client to one user-configured Remote MCP endpoint.

These paths can appear together in one Agent Run but retain separate identity, credentials, authorization, lifecycle, and failure boundaries. The shared [Session, Thread, Run, and Item model](../../interaction-model.md) remains the only Agent interaction model. Connectivity owns no parallel Agent runtime, transcript, active-work queue, or retry lifecycle.

## Specification Catalog

| Document                                                                                          | Owning contract                                                                                                                         |
| ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| [00 Overview](00-overview.md)                                                                     | Subsystem concepts, dependency direction, process placement, and end-to-end flows                                                       |
| [01 Ingress and Routing](01-ingress-and-routing.md)                                               | Ingress identity, provider event normalization, durable admission, input mapping, routing, and Agent Thread binding                     |
| [02 Messaging Ingress](02-messaging-ingress.md)                                                   | Slack, Lark, Discord, and Teams Agent selection, discussion continuation, capability selection, and native messaging boundaries         |
| [03 Connector Providers, Connectors, and Connector Connections](03-connectors-and-connections.md) | Provider definitions and configuration, Connector discovery, connection identity, external credential custody, assignment, and dispatch |
| [04 Agent-Facing Tools](04-agent-facing-tools.md)                                                 | a13n MCP, native actions, direct and catalog exposure, Run tool snapshots, invocation grants, dispatch, and provider receipts           |
| [05 Contract Limits](05-contract-limits.md)                                                       | Explicit provider and product boundaries for which the common contract defines no portable behavior                                     |
| [06 Remote MCP Connections](06-remote-mcp-connections.md)                                         | User-configured Streamable HTTP MCP endpoints, ownership, authentication, OAuth client behavior, lifecycle, and runtime eligibility     |
| [07 Built-in Ingress Adapters](07-built-in-ingress-adapters.md)                                   | Exact Slack, Lark/Feishu, and GitHub App HTTP identities, wire validation, routing, acknowledgements, and native actions                |
| [08 Built-in Connector Provider Adapters](08-built-in-connector-adapters.md)                      | Exact OpenConnector and Composio setup, callback, status, catalog, versioning, and execution profiles                                   |

Read `00` first. Read `01` for every inbound provider, `02` only for conversational messaging, `03` for general outbound SaaS accounts, `04` for every Agent-facing external tool, `05` for the limits of portable cross-provider behavior, `06` for user-configured Remote MCP, `07` for the built-in native providers, and `08` for the built-in Connector Provider implementations.

## Authority Rules

- Provider configuration and wire data remain provider-specific. The common Ingress contract begins only after an installed adapter has authenticated and normalized an event.
- Foundation owns `Ingress`, `Route`, `AgentThreadBinding`, `ConnectorProvider`, `ConnectorConnection`, and `MCPConnection` identity and authorization. An external integration service, not Foundation, owns the third-party account credentials behind its Connector Connections. Discovered Connectors are safe catalog values, not another managed resource. Foundation owns credentials required to operate an Ingress or authenticate as an MCP client.
- Every Ingress binds one same-Workspace Service Account as its immutable execution Principal. Provider actors remain untrusted external context. Admission and every RunAttempt reauthorize that Service Account, the selected Agent, the exact Route and capability selections, and current shared or exactly Principal-owned external resources.
- Foundation Service owns durable Run acceptance, Thread advancement, and active-Run steering. Ingress routing accepts a Run for an idle Agent Thread or Steers one compatible current accepted or running Run or current/head waiting Run, never creates a queued submission, and owns no parallel Agent inbox.
- One inbound messaging event selects at most one Agent. Connectivity never creates implicit multi-Agent fan-out.
- The a13n MCP is the single Foundation-owned MCP server surface for Ingress native actions and Connector tools. User-configured Remote MCP servers remain separate MCP sources selected through `MCPConnection`.
- Ingress native actions are directly model-visible whenever the accepted Run has an authorized native action set. Connector and user Remote MCP tools can use direct or catalog exposure.
- Every Run fixes its exact effective capability selections and MCP tool snapshot. Every RunAttempt resolves fresh credentials and revalidates current authority without changing that accepted surface.
- Inbound receipt and outbound action dispatch are independent completion boundaries. Receiving an event never implies that an Agent replied or that an external action succeeded.

## Authorization

Workspace Admin manages Ingress and ConnectorProvider identities, their credentials, Workspace-shared ConnectorConnections and MCPConnections, and the Service Account selected by an Ingress. Workspace Builder manages Routes, including Agent overrides, safe input mappings, input batching, messaging policy, and per-Agent capability overlays, only when every referenced Agent and capability is currently authorized. Viewer and Runner can read safe metadata according to resource visibility; Runner uses selected capabilities only through an accepted Run and cannot change Connectivity configuration.

An active Workspace User can create and manage a User-owned ConnectorConnection or MCPConnection only for that same User and can use it only through Runs invoked as that User. Workspace Admin can inspect safe metadata, disable, revoke, or delete a Principal-owned resource but never observe its credential. A Service Account can own a ConnectorConnection when the selected ConnectorProvider supports that setup, but it cannot own an interactive User-owned MCPConnection under the current contract.

Every management mutation, setup, credential replacement, OAuth completion, test, disablement, revocation, and deletion calls the common authorizer and records a bounded security audit event. External provider IDs, aliases, matching message content, and possession of an Ingress, Route, ConnectorConnection, or MCPConnection ID never grant authority.
