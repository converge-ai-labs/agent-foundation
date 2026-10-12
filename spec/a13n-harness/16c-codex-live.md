# Codex Live Transport

## Design Position

`a13n_harness.models.codex_live` provides a reusable subscription-authenticated voice-session factory inside Harness. It owns Codex WebRTC bootstrap, the server-side control connection, and typed client-delegation events. The embedding Host owns the media peer, Agent execution, persistence, and presentation. This factory is separate from native request Models and `ExecutableAgent.live()`; it does not introduce another Agent loop.

## Authentication and Connection

`CodexLiveModel` takes a public `OpenAICodexCredentialSource`, a `CodexLiveVoice` name (default `cove`), and optional Harness Run configuration. Voices follow Codex V3; incompatible V2 voices such as `marin` and `cedar` are rejected before loading credentials or creating a call. [Model Authentication](16a-model-authentication.md) owns credential loading, OAuth refresh, and Host storage. A dedicated native provider HTTP client creates the call at the fixed Codex HTTPS endpoint with redirects and transport retries disabled. The speech model uses the Codex runtime preset; the Host's ordinary request Model remains independently selected.

`connect(offer_sdp, instructions=..., on_answer=..., initial_items=...)` is an async context manager. The caller supplies an SDP offer and an asynchronous answer callback. The factory validates bounded SDP input, creates one call, and invokes `on_answer(answer_sdp)` before attaching the sideband. The callback applies the answer and waits for its media peer to connect. Holding the answer until sideband attachment would prevent activation of the upstream call.

After media activation, the factory checks the selected account again and attaches only to the fixed OpenAI Live sideband endpoint for that call ID. Only the successful bootstrap request's authorization/account pair is reused. Redirects cannot forward these credentials. The browser receives SDP, never subscription credentials. No alternate account, API key, Model gateway, or automatic call replacement participates.

## Events and Context

The public event union contains:

- `SessionReady`: provider readiness observation;
- `Transcript(role, text, final)`: user or assistant speech text;
- `Delegation(item_id, text, context)`: client task text plus bounded recent conversation context.

The codec consumes Codex's client-delegation dialect, including `delegation.created` text items. It does not reinterpret public Live metadata-only events as task text. Unknown events are ignored; malformed known events, excessive event size, and provider errors fail with safe application errors. Accumulated transcript context is bounded.

`append_speech(text, delegation_id=None)` sends selected user-facing text on the speakable channel. `append_status(...)` sends commentary. Optional correlation uses the provider delegation ID; ordinary session context needs none. UTF-8 context chunks preserve text boundaries and provider size constraints. Sending context is not proof of audible playback, word-for-word speech, task execution, or durable delivery. Private reasoning is never automatically forwarded.

## Failure and Lifetime

One context-manager entry creates at most one call. Ambiguous bootstrap and context-write failures are not replayed. Cancellation propagates. HTTP and WebSocket errors expose safe status/context without tokens, SDP, or provider response bodies. Sideband HTTP rejection is reported with its status code. Context writes are serialized with close; closing sends the provider close event and releases the socket under bounded cleanup.

A Host must stop its own media peer when the voice session ends. The factory has no durable queue, reconnection state, database, audio relay, or authority to start an Agent Run. Provider availability and account entitlement remain runtime requirements.
