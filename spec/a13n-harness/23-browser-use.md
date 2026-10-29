# Browser Use

## Design Position

Harness provides Chromium browser tools over a Host-authorized CDP connection. The implementation has three responsibilities: protocol and page operations, a reusable tab-access backend, and `BrowserUseCapability` with native tool composition and compact references. Embedded applications and interactive Hosts use the same core.

| Owner              | Responsibility                                                                                                                                      |
| ------------------ | --------------------------------------------------------------------------------------------------------------------------------------------------- |
| Host               | Browser provisioning, process/profile lifetime, CDP endpoint trust, current-Run authorization, scope assignment, viewer access, and recovery policy |
| Chromium runtime   | CDP connection, explicit target/frame sessions, page operations, observations, and protocol events                                                  |
| Browser backend    | Global or owned-tab access, exclusive cleanup ownership, logical selection, popup reconciliation, and operation admission                           |
| Browser Capability | Run binding, model-facing tool schemas and guidance, compact element/tab references, and safe result projection                                     |

A scope can outlive several Runs. Hosts that share a browser use one runtime to coordinate target ownership. Owned-tab access limits browser-tool targets; neither access mode isolates cookies, login state, storage, browser settings, or account identity. Shared profile state is intentional. Host shell authority remains separate from browser tools.

## Binding and Authority

`BrowserUseCapability()` is a trusted code-first definition contribution. `RunBindings.browser_backend` supplies its current-Run `BrowserBackend`; supplying the backend alone does not enable the feature. Missing required dependencies and orphan bindings fail before model work. It is not a Run-attached or built-in declarative Capability. The [Capability source rules](04-capability-model.md#capability-sources-and-authorization) apply.

Trusted embedded code may construct `BrowserUseCapability(backend)` when the executable and backend share an authority boundary. An explicit Run binding overrides that backend. Each native Run binding creates fresh compact-reference state, including recovery attempts within one logical Run. A Run ending leaves the borrowed backend, scope, connection, and browser process alive.

The Host authorizes browser access using the captured current Run, including any Environment override. Browser access operates on the browser Host and does not inherit sandbox isolation. The backend checks live admission on every call and after waiting for another operation. Hosts can bind a revocation check to a scope or implement the backend protocol with a current-Run authority wrapper. A previously allowed Thread, endpoint, or target ID does not grant a new Run access.

A restrictive `allowed_hosts` configuration is rejected: native browser traffic cannot be reliably constrained by the Harness HTTP allowlist. Network isolation belongs to the browser Host. Internal Chromium pages and Host file URLs are accepted. Upload paths refer to the browser Host filesystem, not a remote user's computer or the Agent's sandbox.

## Tab Access, Ownership, and Selection

`CdpBrowserBackend(browser, tab_access="owned")` preserves the default owned-tab behavior. The Host can explicitly select `tab_access="global"` on the same runtime. This is a binding policy, separate from tab cleanup ownership and shared profile state.

| Access mode       | Visible and actionable targets                                           | Existing-page attachment                     | Backend close             |
| ----------------- | ------------------------------------------------------------------------ | -------------------------------------------- | ------------------------- |
| `owned` (default) | Owned page targets and their popups                                      | Claims the exact target exclusively          | Closes owned targets only |
| `global`          | All actual live page targets, including user tabs and other scopes' tabs | Borrows the exact target without claiming it | Closes owned targets only |

Global discovery and selection do not claim tabs. An explicit `browser_tabs(close)` may close any accessible tab; lifecycle cleanup closes only the backend's `targets`. New tabs created by either mode belong to the creating backend. Popups inherit the owner's exact opener ancestry, including when a global binding interacts with another scope's page. A new-tab command from a borrowed page creates an independent target rather than inheriting the borrowed opener's owner. Native tab groups and windows do not define ownership.

Each binding maintains its own logical selection without following or activating the globally foreground tab. Global listing does not select a page implicitly. Target destruction removes ownership and clears affected selections; a binding selects a surviving owned target when available, otherwise the caller explicitly selects or opens another tab. An empty scope remains usable for opening a new tab. Reconciliation uses exact target IDs and opener ancestry; it never adopts tabs by matching title, URL, or native window. Host-only attachment validates an exact live page target and requires fresh observation. One target has at most one cleanup owner, even when several bindings can access it.

Operations serialize within a binding and on each shared Page. After waiting for a Page, admission is checked again. Switching the binding using a Page expires its element and screenshot references and requires that binding to observe before acting; another binding's observation cannot satisfy that requirement. Independent pages may progress concurrently. Cleanup drains other bindings' operations on each owned Page before closing it. Browser-wide configuration, native focus, profile operations, and viewer control remain coordinated by the Host. There is no browser-wide Agent task queue.

## Observation and Tools

The Capability exposes nine tools with distinct discovery, reading, and action responsibilities:

| Tool                 | Contract                                                                                                                                                                                    |
| -------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| `browser_observe`    | Bounded accessibility/DOM structure with roles, names, states, values, hierarchy, frame context, and compact references; filter by role/name/text or referenced subtree, and paginate rows. |
| `browser_read`       | Read the current rendered document or referenced region as text, links, table rows, or form values, with continuation offsets and frame coverage.                                           |
| `browser_navigate`   | Navigate, reload, or traverse history; report dispatch, load verification, and the final observed URL/title.                                                                                |
| `browser_act`        | One discriminated action: click, fill, type, press, hover, check, select, or scroll.                                                                                                        |
| `browser_wait`       | Wait for element visibility, enabled/checked state, rendered text, URL, or document readiness.                                                                                              |
| `browser_tabs`       | List, create, select, or close accessible tabs.                                                                                                                                             |
| `browser_files`      | Upload Host paths to a referenced file input, or inspect accessible tracked downloads.                                                                                                      |
| `browser_dialog`     | Inspect, accept, or dismiss the selected page's JavaScript dialog.                                                                                                                          |
| `browser_screenshot` | Return a JPEG of the viewport, a viewport clip, or the full page, with screenshot identity and CSS dimensions.                                                                              |

Observations combine semantic structure with rendered DOM state. Password values are redacted. Reading uses the active page rather than a separate HTTP request and preserves existing actionable references. Bounded results identify pagination or frame coverage instead of implying the whole document was returned.

The Capability projects backend identities as compact `eN`, `tN`, and `sN` references local to the current binding. Element spellings increase monotonically and never alias a different node. Re-observation can retain references to returned nodes within the same revision; a subtree query also retains its root for subsequent pagination and reading. Other omitted elements expire. Tab references retain identity across listings while the target remains accessible. Main-frame navigation, tab selection, human control, and target replacement invalidate all element references. Subframe navigation or removal invalidates only that frame and its descendants; unrelated references remain usable. Stale actions fail and request fresh observation. Frame references retain their owning session so same-process and out-of-process iframe actions address the observed node.

Actions accept a reference or a semantic locator combining role, name, text, and optional frame index. Locators require exactly one matching target; waits can address a target that has not appeared yet. Each call carries one action, without a batch language. Pointer actions check visibility, hit testing, and target identity before dispatch, including after hover. Keyboard input verifies actual focus and editability, and releases held keys even when another release fails. Check and select verify the resulting control state. Fill replaces text; type sends individual key events and stops if focus leaves its target.

Click and hover also accept a one-shot reference to the latest actionable screenshot and normalized image coordinates. The runtime maps these through the screenshot's CSS clip and viewport, independently of device pixel ratio. Navigation, tab changes, handback, a new capture, or an intervening action expire the screenshot. Viewport changes are checked before dispatch, including between clicks of a double-click. Full-page screenshots are observation-only. A screenshot does not freeze moving page content; semantic references are preferred when available.

Interaction results distinguish `dispatch` (`not_dispatched`, `dispatched`, or `unknown`) from `verification` (`unverified`, `passed`, or `failed`). Navigation and waits also report `timed_out` when their total deadline, including scope admission and readiness, expires. An acknowledged input is not proof of a business outcome. Partial gestures are not replayed; the Agent reads or observes to verify the result before deciding the next action. Tool guidance accompanies the tools and honors `toolset_instructions`. Website content is untrusted model input. When the Host configures browser-level download tracking, owned-tab bindings report their owned targets' records, including downloads initiated by uncommitted subframes and owned popups. Global bindings can inspect all records tracked by the runtime, including records for closed targets. Tracking covers attached pages and known owned popup roots; it is not a historical inventory of downloads before attachment. The result contains `downloads` and an `omitted` count. The 100-record retention limit applies separately to each owner (or each attached unowned target); global access does not merge retention buckets or acquire history-cleanup ownership. Overflow is explicitly counted.

## Human Control and Lifecycle

Human takeover is resource mutual exclusion. The Host pauses every scope sharing the controlled desktop before granting viewer input, keeps newly created scopes paused during takeover, and revokes viewer input before resuming automation. Pausing blocks new calls immediately, drains an admitted operation, and invalidates references. This covers reads and screenshots as well as mutations. Unrelated Agent work can continue.

By default, a tool rejected before dispatch waits for handback and returns a fresh-observation instruction. A trusted Host can supply an explicit `on_human_control` callback to `BrowserUseCapability`: the capability invalidates its references and returns the callback's guidance immediately instead of waiting. The Host owns notification registration and subsequent input delivery. Neither policy replays the original action. Operations already admitted may finish while pause drains them. Read-only wait loops check admission between polls so takeover does not wait for the whole condition deadline. If takeover or retirement interrupts tab creation, the late-created target remains owned for cleanup, and the tool reports interruption rather than navigating or silently retrying it.

The lifecycle boundaries are independent:

- Run completion discards Run-local references and leaves borrowed resources alive.
- Scope retirement revokes admission and wakes handback waiters without closing tabs.
- Scope close retires, drains operations, reconciles membership, and closes owned tabs only. Failed cleanup can be retried.
- Runtime disconnect retires attached scopes, invalidates observations, and closes the CDP client, leaving the browser process and tabs alive.
- Browser process destruction is an explicit Host action.

`CdpBrowserBackend.connect(cdp_url)` is a standalone async context manager. It connects to an existing browser and creates an owned scope; on exit it closes scoped tabs and disconnects its client. It never installs, launches, or destroys the browser process.

## Failure and Recovery

CDP calls are bounded. A timeout, disconnect, or cancellation after dispatch can leave an unknown side effect. Tools do not automatically replay mutations; callers observe and reconcile before retrying. Cleanup failures remain explicit. Scope retirement is irreversible; a replacement binding must be deliberately authorized by the Host.

The Host owns best-effort reattachment across Runs or lost connections. It verifies the browser and exact surviving target identities, attaches a new scope, and obtains fresh observations. Runtime membership and references are process-local observations, not a `HarnessState` checkpoint. Instance restart continuity is not guaranteed and requires no durable browser replay subsystem in Harness.

## Packaging and Invariants

`cdp-use` is a normal Harness dependency. Chromium installation, launch flags, profiles, display servers, and VNC/viewer delivery belong to the Host. The core supports Chromium CDP and has no Playwright runtime dependency.

Tests verify mixed global/owned access, independent selection, shared-page reference invalidation, owned-only cleanup, popup ownership, fresh references across Runs, explicit binding and source admission, concurrent independent scopes, pause/drain/handback, close/new-tab races, external target loss, exact reattachment, cleanup retries, and disconnect without browser destruction. Opt-in native tests exercise the CDP operations against a caller-supplied Chromium executable.
