# Computer Use

## Design Position

Computer use is an optional Session operation family for observing and controlling a shared native desktop. It does not allocate a desktop, acquire ownership, reserve foreground focus, or lock out humans or other agents. The Session owns observation references, temporary image readers and operation evidence, not GUI applications or their state.

Trusted startup must explicitly enable `computer_use` (default `false`). The native implementation supports macOS and Linux X11 with disabled Sandbox and inherited egress only. Wayland and XWayland are unsupported; Linux rejects a Wayland launch environment and servers advertising the XWAYLAND extension. Other platforms, restricted Sandbox and denied or controlled egress reject the opt-in; a worker cannot reach outside its boundary to operate the launcher's desktop. Full Control alone does not enable computer methods. Missing macOS Screen Recording or Accessibility permission is reported, never bypassed or implicitly granted.

[EIP](02-eip-protocol.md) owns method negotiation, Session selectors, operation admission and receipts. [Transfers](04-resource-operations.md) owns raw byte integrity and resource accounting. The [Harness Environment contract](../a13n-harness/08-environment-integration.md#computer-operations) owns model-facing references and exact action permissions.

## Startup Authorization

With computer use disabled, startup neither checks nor requests desktop permissions. On macOS with it enabled, envd checks Screen Recording and Accessibility before starting any EIP transport: no reverse WebSocket connection, HTTP listener or stdio protocol processing starts until both permissions are granted. Host enrollment through `connect` is separate and may precede this local authorization gate.

Missing permissions trigger native authorization requests and a bounded wait. `computer_use_permission_timeout_ms` (default 120000, positive) or `A13N_ENVD_COMPUTER_USE_PERMISSION_TIMEOUT_MS` configures that wait independently of protocol initialization timeouts. Startup diagnostics go only to stderr and identify missing permissions, the System Settings path and possible launcher restart. Timeout, cancellation or a failed check exits unsuccessfully without starting an EIP transport; envd does not fall back to partial desktop capability. A pending native prompt does not extend the authorization deadline indefinitely.

Startup permission readiness is not a permanent grant. Native observation and input recheck permission during operation; revocation fails the affected operation without implicitly reauthorizing it or replaying input.

On Linux, startup uses the explicitly inherited `DISPLAY` and normal X11 authentication (`XAUTHORITY` or the user's default authority file). It requires access to an existing X11 server with RandR 1.5, XKB and XTEST before starting any EIP transport. Missing access fails startup; envd never starts a display server, changes access controls, or falls back to a host display. The same startup timeout bounds readiness, without a permission prompt. A Session keeps one X connection and does not reconnect it; server loss requires a fresh Session and fresh observations.

## Targets and Observations

`computer.describe` returns display targets (`target_id`, name, native width and height) and separate `observe_ready` and `input_ready` observations, plus supported `scroll_units`. X11 advertises only `steps`. macOS omits this additive field to preserve its original strict-client wire shape; an omitted or empty unit list means `pixels` only. X11 requires a client that understands this platform's step-scrolling contract. Method availability is stable startup capability; readiness can change with desktop availability and native permissions.

`computer.observe` accepts an optional target ID and a maximum image dimension (256–2048 pixels, default 1280). Omission selects the primary display (the first active monitor if X11 reports no primary). X11 targets are active RandR monitors on the configured X screen; capture requires a TrueColor root visual and at most 64 megapixels per target. It returns:

- an opaque Session-local `observation_id` and the selected `target_id`;
- actual image width and height, MIME type and capture timestamp;
- a Session-owned raw reader, exact immutable `size_bytes` and reader expiry.

The native implementation produces JPEG images bounded to 4 MiB. Images travel on the existing raw data plane, not as JSON/base64. Screenshot readers share transfer concurrency, staging byte/object limits and SHA-256 completion verification with file transfers. Capture reserves the 4 MiB encoded-image bound and one staging object against both Session and Device budgets before native capture, then shrinks the reservation to the actual image length at reader publication. The charge follows image bytes into the raw producer and is released only when those bytes are dropped. `computer.close_observation` closes the image reader and returns transfer completion; it does not invalidate the geometry reference. Client readers close on success, error or cancellation. Session cleanup releases remaining readers.

Observation geometry records the selected display's native origin and size and the returned image dimensions. Pointer coordinates are nonnegative image pixels with `x < width` and `y < height`; the daemon converts them through this recorded geometry. An unknown, expired or evicted reference, a missing display, or a changed display layout fails before input dispatch. References remain bounded (16 per Session, 120-second lifetime) and do not survive Session replacement.

A reference is not a pixel-freshness guarantee. An application, focus or window may change without changing display geometry. There is no desktop-wide effect counter. The agent must observe again when it needs current content; repeated observation does not eliminate shared-desktop races.

## Input

Each call performs one complete bounded gesture:

| Method                | Input                                                                                                                                            |
| --------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------ |
| `computer.click`      | Observation, point, left/right/middle button, one or two clicks                                                                                  |
| `computer.move`       | Observation and point                                                                                                                            |
| `computer.drag`       | Observation, start/end points, button and 100–3000 ms duration                                                                                   |
| `computer.scroll`     | Observation, point, optional `unit` and signed deltas; `pixels` (default) bounded to ±10000, `steps` to ±100 per axis; positive means right/down |
| `computer.type_text`  | 1–16384 UTF-8 bytes of literal text                                                                                                              |
| `computer.press_keys` | 1–8 explicit physical key names pressed as one chord and released                                                                                |

Text and keys act on current foreground focus and do not select a window or require an observation. Key names include lowercase ANSI letters/digits, punctuation, `meta` (Command/Super), `alt` (Option/Alt), `control`, `shift`, `enter`, `tab`, `escape`, `space`, `backspace`, `delete`, arrows, `home`, `end`, `page_up`, `page_down`, and `f1`–`f12`. Literal text uses Unicode rather than a keyboard-layout guess.

X11 does not advertise `computer.type_text`: temporary Unicode keyboard-map changes race application-side translation. It neither mutates the keymap nor substitutes clipboard paste. `computer.press_keys` resolves physical names through XKB, not layout-dependent keysyms or assumed numeric keycodes. A physical chord is not a literal-text substitute. Missing physical keys fail before dispatch.

X11 scrolling uses XTEST wheel button pairs (4/5 vertical, 6/7 horizontal); steps are discrete wheel increments, not pixel distances. macOS retains pixel scrolling only. Unsupported units fail before pointer movement. New clients send `steps` only when the daemon explicitly advertises it; omission preserves older macOS pixel requests.

There are no persistent key-down or button-down methods. An operation attempts to release only its own pressed keys/buttons on every exit, including interruption. This cleanup is not desktop ownership and cannot promise isolation from concurrent physical input.

## Completion, Interruption and Retry

Describe, observe and reader close use active-only operation admission. Input methods use terminal evidence and the existing mutation ledger; the same retained operation ID/digest can return evidence but cannot dispatch the gesture twice. Neither client nor Harness automatically retries input after a transport failure or ambiguous result.

Native capture and input remain Session-owned after an individual transport waiter disappears. Session close cancels and joins owned work before releasing its scope. Operation cancellation or timeout is not proof of native rollback. Capture interrupted before reader publication does not publish a usable screenshot reader.

An input result contains an `OperationReceipt`, `effect` (`not_executed`, `executed`, `partial`, or `unknown`) and `input_cleanup_complete`. `executed` means native event posting completed, not that an application accepted it or that the user's intended task succeeded. `partial` or `unknown`, failed cleanup and lost responses must remain visible and must not be converted to retry-safe failure. Preflight errors before any event prove no dispatch. Evidence loss after possible dispatch remains unknown even after a fresh Session is opened.

## Compatibility and Invariants

- Methods are additive, negotiated through exact availability; old daemons expose no computer family.
- Computer authority is independent of file and shell access. Neither a cwd nor a file route confines GUI effects.
- A screenshot is an observation, not a file path or authority to control a different mount.
- Closing an observation reader releases image bytes without closing the desktop or its applications.
- Closing a Session cleans its resources without undoing already posted GUI effects.
- No transport reconnect, Run restart, observer error or display-copy failure causes automatic input replay.
