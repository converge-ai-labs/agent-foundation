# Transports and Sessions

## Design Position

Trusted stdio, Host-dialed HTTP(S) and outbound reverse WebSocket carry the same EIP device handshake and independent logical Sessions. A device carrier can remain ready with zero Sessions or carry many concurrently. Envd remains the responder even when it dials a reverse connection. The Host owns product authentication and directory selection; transport routing never creates those permissions.

[EIP](02-eip-protocol.md) owns methods/results and operation evidence. [Resource Lifetime](09-resource-lifetime-and-reclamation.md) owns Session liveness, disconnect grace and collection. This document owns framing, attachment authentication, routing and failure isolation. A Session owns its native resources; ordinary Sessions are not tenant sandboxes. Optional controlled Sessions follow [Execution Isolation](07-execution-isolation.md). A carrier owns delivery only.

## Device and Session Admission

The first stdio/WebSocket application request is `initialize`, with the expected device identity and offered versions. Success establishes one device connection, not an Agent workspace. HTTP performs the same authenticated handshake without creating a server-side TCP-affine connection object. Explicit first-contact discovery is allowed only for Host registration; ordinary acquisition pins the registered identity.

A device-ready requester calls `session.open` with the selected device/generation/version, an optional working directory and required methods. Admission resolves and checks that directory and reserves one Session slot. Success returns a fresh unpredictable generation-bound session selector and descriptor. A failed open changes neither sibling Sessions nor the carrier; partial allocations are cleaned conservatively.

A Session is open/attached, detached, closing or closed. Before the first successful `environment.readiness`, only readiness, keepalive and close are admitted. No provider/model operation scope is published until readiness succeeds. A lost open response can leave an unclaimed Session until idle expiry; clients do not retry open automatically or interpret loss as non-creation.

Every session-scoped control envelope carries `eip_session`; responses echo it. `initialize`, `device.describe`, `directory.list` and `session.open` requests omit it. JSON-RPC IDs are unique among outstanding calls within their scope; device calls use a separate scope. Binary frames carry the same Session selector. HTTP raw transfers use the `EIP-Session` header. Selectors are never URL/query/cookie values or model-facing credentials. A selector is usable only through the authenticated Host that owns the Session.

Closing/expiring one Session fences its new calls and cleans its operations, transfers, commands and output. It does not close sibling Sessions or reset the connection handshake. A writer commit either transfers its candidate and frozen destination authority before close, or remains Session-cleanup-owned. There is no sequential close/rearm barrier.

## Shared Carrier Rules

- One bounded UTF-8 JSON-RPC object per control message; no batch arrays or application notifications.
- One correlated result/error per accepted request unless delivery fails.
- One physical reader/demultiplexer per framed carrier; Session clients never read the same socket independently.
- Independent bounded per-session correlation and transfer queues plus aggregate carrier/daemon ceilings.
- Fair scheduling preserves progress for keepalive, close, cancellation, release and unrelated Sessions; a stalled transfer cannot monopolize control delivery.
- One Session, direction, attachment and exact byte sequence per transfer; transfer handles never change Sessions.
- No fallback/reconnect replay after possible native dispatch.

A caller abandoning a sent request does not immediately free all tracking for that request. The client keeps bounded in-flight ownership until a late response, deadline-driven logical Session teardown or carrier loss settles it. Request IDs are not reused while ambiguity exists. Repeated cancellation cannot exhaust an abandoned-ID cache and force unrelated healthy Sessions off a shared carrier. A routeable Session-local violation closes that Session; corruption that prevents trustworthy frame/session routing may require closing the carrier.

Carrier liveness, Session inactivity and operation/transfer deadlines are distinct. Ping/pong proves only carrier liveness. Session keepalive runs independently of long operations and binary traffic. Native work/output does not establish owner activity. All durations use the owning daemon's monotonic clock; a lost response never proves cancellation or non-dispatch.

## Binary Data-Frame Profile

EIP 0.1 selects binary profile 1. Stdio and WebSocket share this network-byte-order frame; HTTP transfers use raw bodies instead.

| Field               | Width | Contract                                                                         |
| ------------------- | ----: | -------------------------------------------------------------------------------- |
| magic               |     4 | ASCII `EIPD`                                                                     |
| profile version     |     1 | `1`                                                                              |
| frame kind          |     1 | `1=ATTACH`, `2=ATTACHED`, `3=CHUNK`, `4=END`, `5=END_ACK`, `6=RESET`, `7=CREDIT` |
| terminal status     |     2 | Zero except typed RESET status                                                   |
| session byte length |     2 | Positive bounded UTF-8 Session selector length                                   |
| handle byte length  |     2 | Positive bounded UTF-8 transfer selector length                                  |
| stream offset       |     8 | Preceding/accepted byte count                                                    |
| payload byte length |     4 | Raw chunk length, zero for non-CHUNK frames                                      |

The 24-byte header is followed by Session bytes, handle bytes and payload. The entire frame respects `max_transfer_frame_bytes`; lengths are checked before allocation. The selected Session and handle must match the same carrier trust scope and generation. Attach offsets are zero. Gaps, duplicates, reordering, wrong direction, repeated attach or post-terminal data reset the affected transfer; an unrouteable malformed header fails the carrier.

CHUNK payloads are non-empty. Each transfer has a fixed eight-CHUNK send window. The receiver returns a payload-free `CREDIT` with the cumulative consumed byte offset after accepting each chunk. Credit must advance to a sent chunk boundary; duplicates, regressions and unsent offsets reset that transfer. The sender reserves its window slot before publishing a CHUNK, stops when all slots are occupied, and drains all outstanding credit before END. Attachment, CREDIT, END and RESET do not consume chunk slots. Credit releases bounded transport capacity only: it is not integrity verification, successful close, sealed-upload evidence or mutation completion. A stalled consumer backpressures only its transfer until its existing idle/absolute deadline; other transfers and control requests continue. HTTP adapters translate this internal flow control to raw-body backpressure, not extra HTTP requests.

A reader follows `ATTACH -> ATTACHED -> CHUNK* -> END`, then explicit `file.close_reader`. The consumer must drain all chunks and verify byte count/SHA-256 before close is considered successful. Reader END is producer completion, not acceptance, and has no END_ACK. Abandonment sends RESET when possible and never reports successful close. Reader teardown that leaves the Session usable publishes its RESET acknowledgement, unsolicited terminal RESET, or successful HTTP transfer DELETE only after that reader's producer has settled native I/O and released active transfer capacity. Duplicate reader resets join the same cleanup boundary without publishing another terminal frame; they do not wait for sibling producers. If bounded cleanup cannot settle, the daemon may instead fence the Session and publish a typed error RESET while unsettled native work remains charged. That failure notification does not prove successful native cleanup or reusable capacity.

A writer follows `ATTACH -> ATTACHED -> CHUNK* -> END -> END_ACK`, then explicit commit or abort. END_ACK seals the uploaded candidate, not destination publication. RESET before commit cleans Session-owned state; after commit handoff it cannot demote or cancel the accepted operation. Reset acknowledgements/cleanup are finite and scoped; inability to safely route further traffic may fail the carrier, not invent successful teardown.

## Trusted Stdio Profile

Private pipes belong to the trusted Host that launches the expected daemon executable. Each control/data body retains Content-Length framing with bounded header count, line length and total bytes before allocation:

```text
Content-Length: <decimal byte length>\r\n
Content-Type: application/json; charset=utf-8\r\n
\r\n
<one JSON-RPC envelope including eip_session when scoped>
```

Binary content uses `application/vnd.a13n.eip-data` and contains one complete profile-1 frame. A writer never interleaves bytes within an outer frame. Stdout contains protocol only, stderr structured logs only; payload stdout/stderr enter the private output spool.

The first initialize is device-scoped. Thereafter the Host opens/closes independent Sessions without reinitializing the pipes or waiting for another Session to finish. A shared Local Envd Host runtime owns the daemon; a workspace adapter owns only its Session. Explicit standalone ownership still permits a caller to own the entire daemon. Stdin EOF normally triggers owned-daemon shutdown; it is neither successful transfer EOF nor proof of non-dispatch. Stdio has no Bearer token and is not exposed as an unauthenticated network service.

## Host-Dialed HTTP Profile

The dedicated EIP listener requires an Authorization Bearer token on every control/transfer request. Public links use verified HTTPS; scoped loopback/provider-private plaintext can sit behind provider TLS. Tokens and selectors never appear in URLs, cookies, logs, model state or portable provider state. No redirects, browser CORS surface, generic file routes, health endpoint or arbitrary URL fetch are supported.

`POST /eip/control` contains one control envelope. Selector-free initialize/device.describe observes Device info; directory.list performs optional bounded discovery without a Session. Selector-free session.open validates its explicit expected device/generation/version and creates another independent Session. This does not replace any existing Session. Subsequent control calls select via `eip_session` in the envelope, identically to framed carriers. An authenticated selector cannot be rebound to another trust principal or generation.

An admitted request returns HTTP 200 with a typed EIP result/error. Missing/invalid credentials return 401; malformed media/framing 400/415; unknown raw-transfer session/handle 409; oversized bodies 413; capacity rejection 429; unsupported paths/methods 404/405. Pre-admission HTTP rejection proves that request did not dispatch. TCP pooling and affinity have no Session semantics.

Raw readers/writers use fixed transfer resources with protected `EIP-Session`, transfer-handle and direction headers. Reader body completion is producer completion only; typed close verifies acceptance. Writer success confirms sealed upload only; typed commit publishes. Use bounded streaming and backpressure; no content encoding/compression, Range/multipart protocol, base64 file bodies or automatic retry/resume. Body failure aborts the attachment and pre-handoff candidate where owned. `DELETE` on the fixed transfer resource, with the same protected headers and an empty body, applies RESET and returns 204 on accepted cleanup. Clients use it on abandonment even after closing the body: a producer may already have sent all bytes into network buffers. It neither accepts a reader's content nor cancels a handed-off writer commit.

A request disconnect releases only its delivery waiter/body. It does not expire the Session or prove a mutation absent. Session keepalive and idle expiry follow the same owner-liveness policy as stdio/WS. Two authenticated HTTP clients can open and use Sessions concurrently, and closing or expiring A leaves B's active transfer intact.

## Outbound Reverse-WebSocket Profile

The operator supplies a fixed normalized `ws`/`wss` endpoint, protected credential file and optional additional CA roots. Envd dials; the trusted Host authenticates the attachment and binds its expected device identity. The upgrade uses Authorization and exactly `eip.v1`. TLS chain/hostname validation is mandatory for wss; no redirects, embedded credentials or implicit insecure fallback. Plain ws is an explicitly selected trusted-network deployment. Per-message compression is disabled.

The first text message is device initialize. A finite handshake deadline applies even when no Run is waiting. Until handshake success, no session operations or binary frames are admitted. Afterward each text message is one device/session control envelope and each binary message one profile-1 frame. Fragmentation is allowed within the aggregate message bound. Session-specific policy/limits cannot mutate sibling coordinators.

One accepted device attachment remains online across Run boundaries. Session close does not close the socket even when no Sessions remain. WebSocket ping/pong and idle device presence do not maintain Session ownership.

Carrier loss detaches its Sessions, destroys incomplete transfers and starts the short disconnect grace. Envd reconnects with capped jittered backoff and performs a fresh Device handshake. The same Host can explicitly reattach its existing Sessions before grace expiry, or open fresh ones. Reattachment preserves the original working directory/resources, but never restores pending response delivery, resumes transfers or replays operations. Expired or closed Sessions cannot be attached.

Missing/malformed token, upgrade 401/403, invalid TLS/hostname, endpoint policy, redirect or incompatible subprotocol/framing is generation-fatal under the daemon contract. Transient DNS/connect/unavailability/liveness failures are reconnectable. Backoff resets only after a stable connection interval, not after each brief upgrade.

## Control-Service and Browser Boundary

The listener and HTTP requester belong to trusted Host code. The Host owns product-user authorization, device registration, directory selection and cross-process placement. Browser UI does not receive daemon credentials, raw Session/transfer selectors or native operation authority. Product IDs in an EIP parameter cannot replace this trust boundary. Generic EIP is independent of any Service routing or storage design.

## Failures and Compatibility

| Failure                                                            | Effect                                                                                 |
| ------------------------------------------------------------------ | -------------------------------------------------------------------------------------- |
| Device authentication/identity/version fails                       | No usable device connection or Session                                                 |
| One Session open/readiness fails                                   | Close that Session; preserve carrier and siblings unless the carrier itself is corrupt |
| Session idle/grace deadline expires                                | Clean that Session and its resources; preserve siblings and carrier                    |
| Local caller timeout/cancel                                        | End that observation; bound correlation and preserve possible-dispatch evidence        |
| Carrier fails after possible mutation dispatch                     | Unknown outcome unless stronger evidence; no replay                                    |
| Session close races commit                                         | Exactly one candidate owner retains cleanup/publication responsibility                 |
| Completed history exceeds retention or eligible pressure threshold | Reclaim unused terminal records/output, never active command resources                 |

All framed carriers require binary profile 1. Sessions have independent ownership and identical retention semantics across carriers.
