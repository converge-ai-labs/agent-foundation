---
title: Realtime API (Alpha)
description: Start a native voice conversation in Service with OpenAI, Azure OpenAI, Gemini Live, or xAI over WebRTC or WebSocket PCM.
---

Service Realtime connects a client to an Agent with a native live model. Audio and text share one conversation with the Agent's tools and history. The API is **Alpha**; clients should allow additive fields and new event types.

## Start a conversation

Select a [Model](models.md) with a realtime calling API as the Agent's primary model. From your backend, use a [workspace API key](connect-application.md) with `run` access. Fetch capabilities, then request a single-use ticket:

```bash
curl -fsS "$A13N_URL/api/v1/realtime?agent_id=$AGENT_ID" \
  -H "Authorization: Bearer $A13N_API_KEY"

curl -fsS "$A13N_URL/api/v1/realtime/sessions" \
  -H "Authorization: Bearer $A13N_API_KEY" -H 'Content-Type: application/json' \
  -d "{\"agent_id\":\"$AGENT_ID\",\"transport\":\"websocket\",\"draft\":\"Say hello.\"}"
```

Check `can_start` and `transports` before creating a ticket. Availability also reports PCM sample rates, the configured maximum duration and whether the model supports manual interruption. Image attachments are not part of the Alpha input protocol described here; `accepts_images` is `false` and `supported_image_types` is empty.

Pass only the ticket response to the media client. Redeem the ticket within `expires_in` (30 seconds) by connecting to `websocket_path` on the Service origin. Offer the protocol and ticket, in that order:

```js
const endpoint = new URL(ticket.websocket_path, serviceOrigin);
endpoint.protocol = endpoint.protocol === "https:" ? "wss:" : "ws:";
const socket = new WebSocket(endpoint, [ticket.subprotocol, ticket.ticket]);
socket.binaryType = "arraybuffer";
socket.onmessage = ({ data }) => {
  if (typeof data !== "string") return; // Play PCM at audio_output_hz.
  const event = JSON.parse(data);
  if (event.type === "ready") {
    socket.send(JSON.stringify({ type: "text", text: "What can you do?" }));
  }
};
```

`accepted` identifies the new Run. Wait for `ready` before sending text or media. Send `{"type":"stop"}` to hang up; `sealed` confirms the Run's final status. If you miss it, read the Run status before opening another call on that Thread.

## Attach images to a voice call

Attach images before a call or while it is active, with or without text. The Console offers attachments when the selected model supports them:

- OpenAI Realtime and Azure OpenAI Realtime: up to four JPEG or PNG images per message.
- Gemini Live: one JPEG per message, because a typed turn can pick up only the most recent frame.
- GPT-Live and Grok Voice: no attachments. The GPT-Live adapter does not send the typed question and image to the visual backend together.

Each image can be at most 4 MiB. The deployment's upload limits also apply.

API clients read `accepts_images`, `supported_image_types`, and `max_images_per_input` from `GET /api/v1/realtime`. Upload each file with `POST /api/v1/uploads` and promote it with `POST /api/v1/assets`. Pass the asset IDs in the ticket's `draft_asset_ids` for the opening draft. During the call, send `{"type":"input","text":"What do you see?","asset_ids":["ast_…"],"respond":true}` over the WebSocket. Omit `text` for an image-only message. An `invalid_argument` frame with `recoverable: true` means the input was rejected and you can correct it without reconnecting.

## Choose a provider and transport

| Model API         | Provider              | Voice transports      | PCM input / output | Verification in Service                  |
| ----------------- | --------------------- | --------------------- | ------------------ | ---------------------------------------- |
| `openai.realtime` | OpenAI Realtime       | WebRTC, WebSocket PCM | 24 / 24 kHz        | Upstream adapter and deterministic tests |
| `openai.live`     | OpenAI GPT-Live       | WebRTC, WebSocket PCM | 24 / 24 kHz        | Upstream adapter and deterministic tests |
| `azure.realtime`  | Azure OpenAI Realtime | WebRTC, WebSocket PCM | 24 / 24 kHz        | Upstream adapter and deterministic tests |
| `google.realtime` | Google Gemini Live    | WebSocket PCM         | 16 / 24 kHz        | Upstream adapter and deterministic tests |
| `xai.realtime`    | xAI Grok Voice        | WebSocket PCM         | 24 / 24 kHz        | Upstream adapter and deterministic tests |

The matrix reports verification with fake transports and adapters; it does not report live vendor calls. Verify the model and transports you intend to deploy with your own provider credentials.

- **WebSocket PCM:** Send and receive mono signed 16-bit little-endian PCM as binary frames. Resample capture at `ready.audio_input_hz` and play output at `ready.audio_output_hz`. The legacy `ready.audio_hz` alias always means output. Use `ready.supports_interruption` to decide whether to offer manual interrupt.
- **WebRTC:** Create an audio-only `RTCPeerConnection`. Ticket `ice_servers` can supply STUN or TURN configuration; omit entries with no optional username or credential when building the browser configuration. Send the local SDP as `{"type":"offer","sdp":"…"}` and apply the returned `answer` SDP. Wait for both `ready` and the peer connection before starting audio. The browser negotiates its media format independently of the PCM rates reported by Service.

Provider credentials stay in Service. Use official endpoints: `api.openai.com`, `api.x.ai`, `generativelanguage.googleapis.com`, or the Azure OpenAI `resource_endpoint` on `*.openai.azure.com`. Custom realtime gateways, additional headers, and session-affinity headers remain unavailable until the upstream realtime adapters expose WebSocket redirect controls.

## Handle limits and failures

The ticket returns the effective `max_duration_seconds`. Omit the request field to use the server maximum (30 minutes by default), or request at least 30 seconds up to that maximum. A cap over 60 seconds triggers one `limit_warning` one minute before expiry. Service defaults to five minutes of conversational inactivity; speech, text, tool work and approval decisions count as activity. Ongoing foreground tool work and approval prompts suspend idle expiry.

`ending` tells the client to stop capture and playback while Service saves the Run. Inspect `sealed.reason` when present and `error.code` on failures:

| Code or reason                                 | Client action                                                          |
| ---------------------------------------------- | ---------------------------------------------------------------------- |
| `duration_limit`, `idle_timeout`               | The call ended normally. Start a new call if the user wants to resume. |
| `usage_limit_exceeded`, `authority_revoked`    | Wait for budget or access to change before starting again.             |
| `invalid_argument`                             | Correct the request or frame before retrying.                          |
| `realtime_transport_failed`, `worker_draining` | Check the Run and Thread, then obtain a new ticket with backoff.       |
| `realtime_unavailable`                         | Recheck availability and Service or provider readiness.                |
