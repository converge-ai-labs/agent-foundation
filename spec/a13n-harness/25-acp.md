# External ACP sessions

Harness connects to an external Agent Client Protocol (ACP) agent using `a13n_harness.acp`. The external agent owns its model loop, authentication and session transcript. The Host owns the connection's placement and lifetime, the saved ACP session ID, Run authority, and display persistence. The ACP client does not need Stream Protocol, Harness UI, or Service.

## Connection and Run

`AcpSessionRuntime.open(transport)` initializes ACP v1 and owns one connection. `open_session(AcpSessionSetup, saved_session_id=…)` uses `session/resume` if advertised, otherwise `session/load` if advertised. An unsupported recovery fails with `acp_resume_unsupported`; the runtime does not quietly create a replacement session. Updates replayed during load are outside the new Run and do not enter its display. A Host can read auth methods from initialization, call authenticate, and use logout when advertised. The session exposes the peer's typed config options and lets the Host set a selected string or boolean option while idle.

An ACP connection serves one Host Thread and configuration revision at a time. The Host retains the connection across prompts and gives each `prompt` its own `AcpRunBinding`. Callback authority ends on cancellation or settlement; admitted operations settle before the Host releases the original binding. A resident process's Environment scope is separate from the Run's callback Environment. The Host closes the connection on eviction or shutdown; the package does not pool connections or persist credentials or session IDs.

`cancel` sends `session/cancel` for the active turn. Its return indicates local dispatch, while the prompt result establishes the peer's stop reason. Cancellation revokes in-flight permissions and host tools; it does not roll back an operation that already took effect. Local cancellation of the prompt awaiter invalidates the connection for later prompts. During a turn, `steer` only sends the negotiated Harness UI ACP v1 `_a13n/steer` extension. Without it the result is `unsupported`, with no queued prompt or cancel-and-reprompt fallback. Delivery failure is `unknown` when acceptance cannot be established.

## Transports and callbacks

| Surface                             | Contract                                                                                                                                                                                                                                          |
| ----------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `LocalStdioTransport`               | A trusted local command with real stdin/stdout pipes and native process-tree ownership. Stderr is drained separately. Closing the transport closes stdin, allows a short graceful exit, and stops descendants. Environment inheritance is opt-in. |
| `connect_streams` / `StreamChannel` | ACP over an already connected asyncio reader and writer. The Host can also inject an `AcpChannel` into `AcpSessionRuntime.open` and owns admission and pairing.                                                                                   |

Environment process output is a bounded observation, not a live ACP transport. An external or managed host supplies its own live ACP channel; Environment-backed files and terminals are independent of that channel.

`AcpEnvironmentClient` routes ACP file paths through the Run's existing Environment path selection. The Host must expose roots that correspond to the peer's ACP paths. Terminal callbacks use the same Run's process operations; terminal IDs are not transferable to another Run. Terminal output is a bounded UTF-8 suffix; truncation is reported. The Host supplies inline permission and elicitation callbacks and can apply its command reviewer at terminal creation and command permission requests. Missing Environment or interactive authority does not escalate access.

## Host tools and instructions

`AcpHostToolBridge` exposes the Host's selected `AbstractCapability[AgentContext]` and toolsets. It prepares them against the current Run's `RunContext`, applies tool preparation, validation and policy, and keeps the Host's credentials and identity in-process. Host interaction handlers must resolve approvals and questions inline; the ACP prompt stays active until those calls settle. The bridge does not automatically include native file, shell, CodeAct, working-state, or delegation tools.

Same-network agents receive a loopback HTTP endpoint with a random connection-scoped bearer token if they advertise HTTP MCP. Other local agents get a thin stdio forwarder to the same endpoint. Across network boundaries, the Host exposes tools only when the peer advertises MCP-over-ACP; the implementation matches the `agent-client-protocol==1.0.0` `mcp/connect` and `mcp/message(connectionId, …)` profile. When capabilities are required but that profile is unavailable, the connection fails with `acp_host_tools_unsupported`. The Host can omit the bridge for a pure ACP session. HTTP, stdio, and ACP share one dispatcher.

The Host supplies a prepared discovery scope to `open_session` when a bridge is configured. The bridge snapshots function-tool names, descriptions, and input schemas before sending the session request. `tools/list` reads that catalog during setup, idle periods, and prompts. `tools/call` requires an active prompt and rechecks the tool against that Run's authority.

A host tool call uses the ACP-reported `toolCallId` on MCP-over-ACP, or matches active ACP calls by programmatic name. An exact name takes precedence; if there are no exact matches, the bridge recognizes `mcp__harness-host__`, `mcp_harness-host_`, `harness-host/`, and `harness-host.` prefixes. Only a unique match supplies the ID; otherwise the Run ID is the turn anchor. The bridge does not emit tool cards; Stream Protocol derives them from ACP updates.

The Host can collect static capability instructions with `AcpHostToolScope.instructions()` and supply them in `AcpSessionSetup`. An advertised `systemPrompt` receives append mode on session setup. Without this capability, static text leads the first prompt of a newly created session. A resumed or loaded session keeps its existing instruction history. `prompt_context()` projects the Run's dynamic Host context using the existing Harness middleware. Context sent with `prompt` is not authored user history. Display projects the Host's authored input, not ACP user-message echoes containing hidden context.
