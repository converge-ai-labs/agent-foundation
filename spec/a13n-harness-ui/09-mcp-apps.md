# MCP Apps Host

## Design Position

Harness UI hosts MCP Apps in its WebUI. An App is an interactive presentation of an actual MCP tool result, not an Agent, a new execution engine, or a browser-control session. The Python App owns MCP connections, retained originals and interaction authority; the authenticated browser owns a View and trusted human confirmation. The MCP server owns its business state.

[Composition](02-agent-composition-and-snapshots.md#mcp-servers) owns server selection and transport recipes. [App and surfaces](05-runtime-subagents-and-surfaces.md) owns ordinary input, execution and HTTP access. [Local storage](03-local-storage-and-recovery.md) owns immutable objects and selected continuation. This contract owns the MCP Apps exception to Run-local MCP transport lifetime and its browser protocol, presentation and follow-up operations. It does not grant browser automation, native Host access or additional Agent Environment authority.

## Opt-in and Compatibility

The root `webui.mcp_apps` mapping defaults to `enabled: false`, `servers: []`, and a loopback sandbox listener with an ephemeral port. `servers` is an allowlist of already selected server IDs, not another selection mechanism. Omission preserves ordinary MCP behavior. Listener settings take effect on WebUI restart. Current accepted configuration and current Thread/Agent selection govern new App interactions; they do not rewrite an admitted Run's immutable composition.

The supported profile uses MCP Apps `ui.resourceUri` and `text/html;profile=mcp-app`, with classic MCP tool calls. Task-only execution is not exposed as an App operation. Tool visibility separates model-visible from App-visible tools; App-only tools remain subject to the Host's tool allowlist and permission rules. The browser uses the public MCP Apps bridge and advertises only implemented capabilities: server tools, server resource reads, text/structured context updates, text messages and external links. It supports inline display and bounded size changes. Image contributions, App-provided model tools, browser-control integration and stable-origin-dependent storage are not advertised.

## Identities and Independent Lifetimes

| Value                 | Owner and meaning                                                                                                                |
| --------------------- | -------------------------------------------------------------------------------------------------------------------------------- |
| Server binding        | Current selected server recipe and resolved transport credentials for one owning Thread                                          |
| Connection generation | One live process-local MCP session for `(thread_id, server_id)`; never durable execution authority                               |
| App reference         | Small retained identity naming the actual Thread, Run, tool call, server, tool and immutable original snapshot                   |
| Original snapshot     | Immutable tool descriptor, exact arguments, raw MCP result, original resource reference and connection generation                |
| View                  | Explicitly activated process-local interaction binding to an original, connection generation and current owning root/child route |
| Operation             | One exact follow-up tool request, its policy/approval decision, result and optional auxiliary review usage                       |
| Context reference     | Exact latest context value in one View; replacing or discarding the value invalidates its old selector                           |
| Message receipt       | Process-local single-consumption admission result for one user-confirmed App message                                             |

Multiple Views can share one connection. They do not share follow-up result histories, pending approvals, context selection or message proposals. A View is not the connection and closing it does not close that connection. An ordinary Run still receives a fresh Toolset adapter; an Apps-enabled adapter borrows the App-owned connection rather than creating another live MCP session.

Connections have no idle TTL. Run completion, browser disconnect and View closure do not destroy server state. Explicit connection close, binding retirement, Thread disposal and Host shutdown end the relevant lifetime. Retiring a generation immediately prevents new dispatch; admitted work keeps its borrow until completion. Broken generations are not transparently reconnected and business calls are not replayed. Later explicit activation may start a new connection, but cannot recover the old server's private state.

Tool/resource discovery is refreshed through the live connection rather than a stale per-Run cache. Run-local notification handlers must not replace the connection's stable Host handlers. MCP sessions, process handles and resolved credentials are never serialized.

## Original Capture and Presentation

The Host captures the raw public MCP result at the real tool invocation boundary, before model-facing conversion loses presentation metadata. It associates that result with the real Run and tool-call identity. It publishes bounded immutable resource and snapshot objects, then attaches only small recognized App references to the actual tool return and live presentation. Raw JSON, structured content and recognized resource metadata retain their protocol meanings; UI capture does not change model-facing tool content.

Resource or storage failure after tool success does not retry the tool or turn its successful model result into a failure. The card instead records an unavailable presentation and leaves the ordinary result visible. Existing history without App references remains readable.

Live and saved projections use the same App identity so checkpoint publication does not replace the mounted iframe. Apps are visible inline outside collapsed execution details. Saved history executes no HTML until the user opens the App. Opening loads only the exact retained original; it never connects to the MCP server or repeats the original tool call. Access requires the exact reference to be retained by that Thread's selected history, child inspection history or bounded current live projection. Knowing an object reference is not membership authority.

Opening a presentation is separate from activating server interactions. Activation resolves current authority, validates the original tool contract and resource against the selected server, and returns a View. Changed contracts require a new ordinary tool invocation, not silently substituting new HTML into an old presentation. Follow-up results stay in the View and never replace the saved original.

The Host does not save DOM, iframe heap, browser storage, pending RPC promises or user navigation inside the App. Restart/reopen can show the original HTML and result, not resume the old UI or promise offline availability for external assets.

## Current Authority and Tool Operations

Every operation resolves the current owning root and selected Agent graph. A child View follows its recorded delegation route through the current root graph; an unresolved or changed route fails rather than using a historical child composition as authority. Current tool visibility, original-tool compatibility, server selection, effective credentials, JSON argument schema and permission policy are checked before dispatch. A same-server resource URI or operation name never authorizes another server or Host filesystem access.

Tool requests carry a View-local request key, exact name and captured JSON arguments. Reusing the key with the same payload returns the existing operation; a different payload conflicts. Admission retains the request before asynchronous work. The connection dispatch lane serializes the final authorization and borrow with actual MCP I/O. A queued lane wait is not a durable business queue; retirement or changed policy before dispatch fails the operation.

Operations move from `checking` to `approval_required`, `running`, `denied` or `failed`; running completes as `completed` or `failed`. Native permission calculations are shared with Harness rather than bypassed through a fabricated Run or Agent context. `ask` displays the exact server, tool, arguments and reason outside the iframe. Review policy uses real auxiliary Model admission and usage accounting, independent of a conversational Run. Review failure follows the selected policy.

An approval is bound to the View, exact payload and observed admission contract. It is consumed once, then current authority is checked again inside the dispatch lane. Approval does not override a later deny, credential change, schema change or View closure. After a lost decision response, the browser only queries the existing operation; it cannot send an opposite decision or silently repeat the original decision. Closing a View invalidates unsent approvals, not effects already dispatched.

The browser retains at most 128 tool requests per View, each at most 256 KiB. The Host bounds live Views, retained operations, results and aggregate process-local bytes. Limit exhaustion is visible and asks for explicit closure/reopening; it does not silently evict undecided work or replay it.

## Resource Reads

An App can read resources from its selected server when the URI is currently listed, matches a supported current resource template, or was explicitly returned in protocol `resource_link`/embedded-resource content in its original or follow-up results. Arbitrary URLs buried in structured data are not resource grants. Template matching requires an exact parse/expand round trip: unknown or duplicate query parameters and unsupported template operators are rejected. The initial supported subset follows the locked MCP resource-template implementation, not a claim of full RFC 6570 support.

Reads share the connection dispatch lane and recheck current authority after discovery. The Host invokes MCP `resources/read`; it does not fetch arbitrary HTTP URLs or interpret paths as Host files. Requests and response sizes are bounded; each View retains at most 256 explicit returned resource references.

## Explicit Model Context

`ui/update-model-context` replaces a View's latest text/structured value, bounded to 64 KiB. It does not start a Run, change a shared draft or automatically enter model history. The trusted Host card permits inspection, selection and discard. Selection belongs to mounted Views in that browser conversation, not server presence, another tab or the collaborative draft.

Ordinary composer submission captures selected exact context references at Send, before asynchronous draft preparation. The Host captures immutable values before awaiting authority checks, accepts at most eight distinct Views belonging to the target root, rejects stale/missing selectors, and appends source-attributed external-data text through the normal ordered input path. App content is labeled as external data, not Host instructions. Later context updates cannot rewrite that captured input. Context is not implicitly attached to steering. Closing a View or discarding context clears selection; an in-flight selected update blocks capture rather than silently sending an older value.

## User-confirmed App Messages

`ui/message` is a proposal, not immediate execution permission. The trusted card displays the exact text, destination root and any selected own-View context before Send once or Decline. Only one unresolved proposal is allowed per View. The iframe cannot click its own Host confirmation. A child App explicitly proposes an attributed handoff to its owning root; it never fabricates user input to the child.

After confirmation the browser sends the immutable request once. The Host reserves a receipt keyed to that payload before asynchronous admission, verifies current owner/binding, captures any exact selected context and invokes ordinary root submission. It rechecks the App's current authority after ordinary input preparation immediately before root admission. Busy roots fail through normal admission; no hidden queue, steering or composer mutation is added.

The receipt is `submitting`, `accepted` with the ordinary root receipt, or `failed` with a reason. An accepted message is admission, not Run completion or durable scheduling. Exact repeated requests return the same receipt; conflicting payloads fail. A lost response is reconciled by receipt reads only, with an explicit unknown-outcome state when unavailable. A stale read for an older proposal cannot settle a newer proposal. Closing the View rejects its local waiter but does not abort a root Run already admitted. Receipts are process-local and may be released after closed-View cleanup; the conversation is the evidence for any completed work.

## Browser Isolation and External Links

The authenticated Host embeds a separate-origin credential-free proxy. That proxy contains a script-enabled opaque-origin inner iframe without same-origin, form, top-navigation or popup privileges. It validates exact parent/source origins, forwards only between its parent and inner View, and applies an HTTP CSP derived from bounded exact resource-declared origins. App content cannot weaken this policy through HTML meta tags. Host API routes remain authenticated and reject opaque/cross-origin browser requests; no Host key, cookie value, MCP credentials or unrestricted client is handed to the iframe.

The proxy serves only its sandbox document, not Host APIs, files or login. Host and sandbox origins must differ. Default CSP grants no network connections; declarations cannot grant the Host origin. The initial profile accepts exact HTTP(S) origins, not wildcard source lists. CSP and iframe privileges are independent of Agent Environment permissions.

External-link proposals accept absolute HTTP(S) destinations without embedded credentials, excluding the Host and sandbox origins. The trusted Host displays the normalized exact destination and opens it only from the user's confirmation click in a new tab with `noopener,noreferrer`. There is no automatic Host navigation or privileged-route bridge. Decline and View closure reject the proposal.

Theme and locale updates use Host-context notifications without remounting the iframe or replaying tool results. Initialization failure is visible alongside the still-available ordinary result; explicit reopen retries presentation only.

## Listener and Distribution

The sandbox listener belongs to the WebUI process, not an Agent Environment or an external browser-control runtime. Loopback development can allocate an ephemeral separate port. Non-loopback WebUI requires an explicit browser-visible `sandbox.public_url`; `sandbox.bind` and `port` describe the server-side listener, while `public_url` is an exact externally reachable origin. Remote clients must not be pointed at server-only loopback addresses. HTTPS deployments need an HTTPS sandbox origin to avoid mixed content. Reverse proxies must keep the sandbox on a distinct credential-free origin, preserve its CSP and never forward that origin to Host API/auth routes.

Missing remote-origin configuration fails startup. A sandbox socket bind failure leaves the ordinary WebUI usable and reports App presentation unavailability instead of falling back to same-origin HTML. The listener closes on normal Host shutdown. No new daemon, persistent action queue or separate frontend release is introduced. The wheel and sdist include the proxy document and prebuilt WebUI bridge; installed use and wheel rebuilding from sdist require no Node.js.

## Verifiable Invariants

1. Original tool success, retained presentation, View activation, follow-up effect, message admission and ordinary Run completion are distinct facts.
2. No read/open/reconnect path repeats a business call or infers current authority from saved history.
3. Closing one View leaves the shared MCP session and other Views intact, while invalidating its pending consent and context.
4. Changed current policy, child route, binding or schema prevents new dispatch even after a user approval.
5. Context and messages reach model input only through explicit selection/confirmation and ordinary root admission.
6. Uncertain writes are read-reconciled, never automatically resent; receipts do not promise crash recovery.
7. App HTML cannot access authenticated Host APIs, top-level navigation or trusted confirmation controls.
