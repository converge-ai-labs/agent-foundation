# External Connectivity

## Design Position

The Connectivity subsystem lets Foundation-managed Agents receive authenticated external events and use authorized external tools without forcing provider payloads, credentials, or action schemas into one universal model. It owns the stable boundary between provider-specific Ingress adapters, Foundation Run acceptance, external integration services, user-configured Remote MCP endpoints, and the Agent-facing tool surface.

Connectivity has three distinct access paths:

- an Application Account owns a concrete provider identity and authorized native actions, with embedded optional event reception and exact AccountTargets;
- a `ConnectorProvider` discovers available `Connector` integrations and supplies general outbound SaaS tools through safe `ConnectorConnection` references while retaining third-party credentials outside Foundation; and
- an `MCPConnection` lets Foundation act as an OAuth-capable MCP client to one user-configured Remote MCP endpoint.

These paths can appear together in one Agent Run but retain separate identity, credentials, authorization, lifecycle, and failure boundaries. The shared [Session, Thread, Run, and Item model](../../interaction-model.md) remains the only Agent interaction model. Connectivity owns no parallel Agent runtime, transcript, active-work queue, or retry lifecycle.

## Specification Catalog

| Document                                                                                          | Owning contract                                                                                                                         |
| ------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------- |
| [00 Overview](00-overview.md)                                                                     | Subsystem concepts, dependency direction, process placement, and end-to-end flows                                                       |
| [01a Application Accounts](01a-application-accounts.md)                                           | Concrete external identity, credentials, lifecycle, authorized default tools, and reception independence                                |
| [01 Event Reception and Routing](01-ingress-and-routing.md)                                       | Exact targets, fixed input projection, ordered durable batches, atomic Run/Steer acceptance, and Thread binding                         |
| [02 Messaging Ingress](02-messaging-ingress.md)                                                   | Slack, Lark, Discord, and Teams Agent selection, discussion continuation, capability selection, and native messaging boundaries         |
| [03 Connector Providers, Connectors, and Connector Connections](03-connectors-and-connections.md) | Provider definitions and configuration, Connector discovery, connection identity, external credential custody, assignment, and dispatch |
| [04 Agent-Facing Tools](04-agent-facing-tools.md)                                                 | In-process a13n MCP, per-source capabilities, Harness loading and discovery, current authority, dispatch, and provider receipts         |
| [05 Contract Limits](05-contract-limits.md)                                                       | Explicit provider and product boundaries for which the common contract defines no portable behavior                                     |
| [06 Remote MCP Connections](06-remote-mcp-connections.md)                                         | User-configured Streamable HTTP MCP endpoints, ownership, authentication, OAuth client behavior, lifecycle, and runtime eligibility     |
| [07 Built-in Ingress Adapters](07-built-in-ingress-adapters.md)                                   | Exact Slack, Lark/Feishu, and GitHub App HTTP identities, wire validation, routing, acknowledgements, and native actions                |
| [08 Built-in Connector Provider Adapters](08-built-in-connector-adapters.md)                      | Composio account lifecycle and tool execution; OOMOL OpenConnector personal/self-hosted runtime authority and API profile               |

Read `00` first, then `01a` for Application Account identity and lifecycle. Read `01` for every inbound provider, `02` only for conversational messaging, `03` for general outbound SaaS accounts, `04` for every Agent-facing external tool, `05` for the limits of portable cross-provider behavior, `06` for user-configured Remote MCP, `07` for the built-in native providers, and `08` for the built-in Connector Provider implementations.

## Authority Rules

- Provider configuration and wire data remain provider-specific. The common Ingress contract begins only after an installed adapter has authenticated and normalized an event.
- Foundation owns Application Account, `AccountTarget`, `AgentThreadBinding`, `ConnectorProvider`, `ConnectorConnection`, and `MCPConnection` identity and authorization. An external integration service, not Foundation, owns the third-party account credentials behind its Connector Connections. Discovered Connectors are safe catalog values, not another managed resource. Foundation owns Account credentials and credentials required to authenticate as an MCP client.
- Reception uses the Account's current same-Workspace execution Service Account. Ordinary Runs resolve the current target Agent and narrow override; active and selected waiting Runs receive only Steer. External actors remain input context.
- Foundation Service owns durable Run acceptance, Thread advancement, and active-Run steering. Ingress routing accepts a Run for an idle Agent Thread or Steers one current accepted or running Run or current/head waiting Run, never creates a queued submission, and owns no parallel Agent inbox.
- One inbound messaging event selects at most one Agent. Connectivity never creates implicit multi-Agent fan-out.
- The a13n MCP supplies Application Account actions, Ingress native actions, and Connector tools through per-source in-process groups inside the executing Worker or Runner. User-configured Remote MCP servers remain separate MCP sources selected through `MCPConnection`.
- Native actions are default, directly visible tools from protected Run contexts. Agent configuration selects only Connector and user Remote MCP tools; these independently choose Harness deferred capability loading.
- Every Run fixes effective source selections, tool scopes, and protected native context. Every RunAttempt discovers current external tool definitions, resolves eligible credentials, and revalidates current authority under those selections.
- Inbound receipt and outbound action dispatch are independent completion boundaries. Receiving an event never implies that an Agent replied or that an external action succeeded.

## Authorization

Workspace Admin manages Accounts and credentials, ConnectorProviders, ConnectorConnections, and MCPConnections. Workspace Builder manages exact AccountTargets within current Agent and capability authority. Safe-read and Run-use rights follow Workspace IAM; every connection belongs to its Workspace, with no personal owner branch.

Every management mutation, setup, credential replacement, OAuth completion, test, disablement, revocation, and deletion authorizes and records a bounded security audit event. Provider IDs, display names, event content, and possession of a Foundation resource ID never grant authority.
