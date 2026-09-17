# Built-in Ingress Adapters

## Design Position

The first built-in HTTP adapters are Slack, Lark/Feishu, and GitHub App. Each owns one exact installation identity, wire authentication profile, event vocabulary, exact target identity, safe event projection, correlation references, acknowledgement behavior, and native action set. They reuse the common [Ingress and durable admission](01-ingress-and-routing.md) kernel without sharing a provider payload model.

Slack and Lark/Feishu support HTTP callbacks and persistent WebSocket event reception. GitHub App uses HTTP webhooks. Repository webhooks authenticated by a shared secret plus PAT and GitHub Discussions are outside these profiles.

All adapters reject unknown configuration versions and request bodies beyond their limit before JSON parsing. They compare authentication values in constant time, suppress their verified bot identity, and never place raw bodies, message content, signatures, tokens, private keys, callback handles, or provider errors in logs, traces, metrics labels, audit details, or public errors.

## Slack HTTP v1

### Identity and credentials

`provider_key = "slack"` and `provider_config_version = "slack_http_v1"` select this profile. The immutable Account provider configuration contains `api_app_id`, exactly one installed `team_id`, optional `enterprise_id`, verified `bot_user_id`. The mutable `event_transport` is `http` (default, including omitted legacy values) or `websocket`. A changed App, installation, enterprise scope, or bot identity creates another Application Account.

The Slack Account owns write-only credential fields `signing_secret`, `bot_token`, and optional `app_token`. HTTP requires the signing secret; Socket Mode requires an app-level token with `connections:write`. Both require the bot token. The signing secret authenticates webhooks. The bot token is used only for the enabled native Slack operations against `https://slack.com`; it never authenticates inbound delivery.

### Authentication and events

The raw body is limited to 1 MiB. Before parsing JSON, the adapter requires `X-Slack-Request-Timestamp` and `X-Slack-Signature`, rejects a timestamp more than five minutes from current UTC, and verifies `v0=` plus the lowercase hexadecimal HMAC-SHA256 of `v0:<timestamp>:<raw-body>` under `signing_secret`. After parsing, it verifies the configured `api_app_id`, team, enterprise scope, and event authorization against the concrete installation.

An authenticated `url_verification` request returns its exact bounded challenge and creates no admission. An authenticated `event_callback` uses `event_id` as its durable external event identity. Retry number and reason headers are bounded observations and do not change identity. Reuse of one retained `event_id` with a different canonical body digest fails closed.

The supported input events are:

- `app_mention`;
- `message` in `im` conversations; and
- `message` in public channels, private channels, or multi-person direct messages only when the Account installation has the required event subscription and the selected reception interaction mode permits `chat` or `discussion`.

Slack `app_mention` events may omit `channel_type`; these normalize as channel traffic. Ordinary `message` events require an explicit supported conversation kind. This routing classification does not establish public/private audience or grant memory access; current provider verification owns that authority.

The adapter ignores its verified `bot_user_id`, messages with a `bot_id`, the `bot_message` subtype, edited or deleted message subtypes, message bodies containing no content after the exact App mention is removed, and every unsupported event or subtype. Ignored events are acknowledged without admission.

Safe normalization exposes bounded user and conversation IDs, display labels when already present, conversation kind, timestamp, thread relationship, mention facts, and text with the App mention removed. It does not fetch profile or history data during receipt. Stable references are:

- Conversation: `<team_id>:<channel_id>`;
- Discussion: `<team_id>:<channel_id>:<root_thread_ts>`, where a top-level activation uses its own message `ts` as the root;
- Message: `<team_id>:<channel_id>:<message_ts>`.

An exact target uses `target_kind="conversation"` and one canonical Slack channel ID. Supported event types and conversation kinds are adapter-owned; no event/content match expression is configured. The fixed common projection retains safe text, actor, conversation, and discussion context.

An irrelevant, challenge, or already-known delivery returns HTTP `200`. A newly eligible event returns HTTP `200` only after durable admission. Invalid authentication returns `401`, malformed or unsupported authenticated input returns `400` when it cannot be ignored safely, and unavailable durable capacity returns `503` with `Retry-After`. The request handler performs no Slack Web API call and returns within Slack's three-second acknowledgement window under healthy local dependencies. Deduplication evidence is retained for 24 hours.

### Native actions

The installation requests only scopes required by enabled events and actions. These can include `app_mentions:read`, the matching `channels:history`, `groups:history`, `im:history`, or `mpim:history` scopes, `chat:write`, the corresponding conversation read scopes, and `users:read`. Disabling an action removes it from later Run selections even if the token still has its scope.

| Action key            | Model arguments                                                                         | Hidden current-context binding    | Provider operation                                                   |
| --------------------- | --------------------------------------------------------------------------------------- | --------------------------------- | -------------------------------------------------------------------- |
| `slack.reply`         | bounded `text`; bounded placement enum only when reception policy is `auto`             | channel and root thread timestamp | `chat.postMessage`                                                   |
| `slack.list_members`  | `limit` from 1 through 100 and opaque cursor                                            | current channel                   | `conversations.members`, with bounded cached `users.info` enrichment |
| `slack.read_messages` | `scope` in `conversation` or `discussion`, `limit` from 1 through 15, and opaque cursor | current channel and thread        | the applicable conversations history or replies operation            |

The model cannot provide a team, channel, thread, message, token, or Account identifier. Forced `conversation` or `discussion` reply policy omits placement from model arguments. A reply receipt contains only the returned channel, message timestamp, root thread timestamp, and safe request correlation. A lost response after possible write dispatch is `outcome_unknown` and is not retried automatically. Reads surface bounded rate-limit evidence and do not sleep through a long `Retry-After` inside one tool call.

## Lark/Feishu HTTP v1

### Identity and credentials

`provider_key = "lark"` and `provider_config_version = "lark_http_v1"` select both brands. Immutable Account configuration contains `brand` in `feishu` or `lark`, an official or exact operator-allowed `open_api_origin`, `app_id`, installed `tenant_key`, verified `bot_open_id`. The mutable `event_transport` is `http` (default, including omitted legacy values) or `websocket`. Brand and origin are explicit configuration rather than separate provider keys.

The Lark Account owns write-only credential fields `app_secret`, `encrypt_key`, and `verification_token`. HTTP requires `verification_token`; `encrypt_key` is optional for unencrypted HTTP delivery. Long connections require `app_secret` and do not use the HTTP verification token or encryption key. A short-lived tenant access token is derived from `app_id` and `app_secret`, refreshed before provider expiry through async single-flight, and retained only in Attempt-scoped process memory; it is not another durable credential.

### Authentication, decryption, and events

The raw body is limited to 1 MiB. When `encrypt_key` is configured, the adapter requires the timestamp, nonce, and signature headers, rejects a timestamp more than five minutes from current UTC, and verifies the lowercase SHA-256 digest of the exact concatenation `<timestamp><nonce><encrypt_key><raw-body>`. For an encrypted envelope it base64-decodes `encrypt`, derives the AES-256 key as SHA-256 of the UTF-8 encryption key, uses the first 16 ciphertext bytes as the IV, decrypts AES-CBC, and strictly validates PKCS#7 padding before parsing the inner JSON.

The adapter then verifies the v2 event header token, `app_id`, and `tenant_key` against the Account. URL verification returns the exact bounded challenge only after those checks and creates no admission. `header.event_id` is the durable external event identity; reuse with different canonical payload evidence fails closed. Deduplication evidence is retained for 24 hours.

The supported input event is `im.message.receive_v1`. The installation enables only the application permissions needed by configured behavior: message receive/read, send-as-bot, and chat-member read. Safe normalization accepts text and post messages as bounded text projections. Image, file, audio, video, and other message types expose only bounded type and attachment metadata and cannot materialize binary content. The view can include bounded sender ID and kind, message ID, root, parent and thread IDs, chat ID and type, exact mentions, message type, text, and provider creation time.

Stable references are:

- Conversation: `<tenant_key>:<chat_id>`;
- Discussion: `thread_id` when present, otherwise `root_id`, otherwise `message_id` for an activating group message or `chat_id` for a direct message;
- Message: `<tenant_key>:<message_id>`.

The adapter removes only mentions whose provider identity exactly matches `bot_open_id`, ignores mention-only input and messages sent by that bot, and never equates a sender email or display name with a Service Principal. An exact target uses `target_kind="conversation"` and one canonical chat ID. The fixed common projection retains safe post/text, actor, chat, and discussion data.

An irrelevant or duplicate delivery returns HTTP `200` with the bounded success body `{"codemsg":"success"}`. URL verification returns HTTP `200` with only `{"challenge":"<exact challenge>"}`. A newly eligible event returns the success acknowledgement only after durable admission. Invalid signatures, encryption, token, App, or tenant identity fail without admission; malformed authenticated input fails with a bounded error; exhausted durable capacity returns `503` with `Retry-After`. The receipt handler performs no Open API call. Provider timestamp is retained as ordering evidence after the independent five-minute request freshness check.

### Native actions

| Action key           | Model arguments                                                             | Hidden current-context binding       | Provider operation                                         |
| -------------------- | --------------------------------------------------------------------------- | ------------------------------------ | ---------------------------------------------------------- |
| `lark.reply`         | bounded text or post content; bounded placement enum only for `auto` policy | message, chat, and discussion policy | reply to the current message or create in the current chat |
| `lark.list_members`  | bounded limit and opaque page token                                         | current chat                         | list current chat members                                  |
| `lark.read_messages` | bounded scope, time/order options, and opaque page token                    | current chat or discussion           | list messages for the bound container                      |

The model cannot provide an App, tenant, chat, thread, message, token, or Account identifier. Write calls carry a stable provider UUID derived from the tool effect identity. Receipts expose only bounded message or member projections and provider message identity. A provider response lost after possible dispatch is reconciled only through that UUID or authoritative provider evidence; otherwise the result is `outcome_unknown`.

## Persistent event connections

Connectivity owns outbound WebSocket connections, discovery, credential refresh, heartbeat, reconnect, and shutdown. Only the `connectivity` and `all` process roles run them. Run, Worker, Agent configuration, and Memory carry no transport choice or socket lifecycle. The historical `slack_http_v1` and `lark_http_v1` profile identifiers remain valid for both transports; identity excludes `event_transport`.

Slack calls `apps.connections.open` using the app-level token and verifies the connected App identity from the hello envelope. One connection is owned per Slack App, shared by its configured installations. Events route only to accounts matching the exact App and team; the normalizer also verifies installation facts. Configured accounts for that App must provide the same app-level token. Conflicting credentials prevent connection and produce a bounded diagnostic. Slack's upstream Socket Mode setting is app-wide: operators must coordinate the mode across every installation, including applications outside this Service.

Feishu/Lark long connections support enterprise custom applications at the official Feishu and Lark API origins. Service discovers the endpoint with App ID and App Secret, speaks the provider's binary frame protocol, and routes events only to the configured App and tenant. Store-app ticket lifecycles and custom WebSocket origins are unsupported. Bounded frame reassembly expires incomplete groups after five seconds; at most 32 fragmented messages, 128 parts per message, and 1 MiB of pending payload are retained per connection.

Discovery uses HTTPS without redirects. Returned socket URLs must use WSS on the provider's approved domains; redirects are rejected and destination addresses are checked by the Service endpoint policy. Tokens, temporary socket URLs, raw payloads, and provider exception bodies never appear in ordinary diagnostics.

A durable app-scoped lease chooses one Service owner, with a monotonically increasing generation. Leases last 30 seconds and renew every eight seconds. Each event admission locks and validates the current lease within the same short transaction as the Account version, credential generation, capacity checks, deduplication and durable append. No database session spans network I/O or socket waits. Loss of ownership stops the connection; stale owners cannot admit events or renew a successor's lease. Configuration, rotation, disable and deletion are reconciled every five seconds and invalidate old admission snapshots immediately.

The socket receiver acknowledges eligible input only after durable admission. Slack echoes the envelope ID; Feishu returns the success frame. Agent execution and reply delivery happen independently after acknowledgement. Ineligible or unconfigured events are acknowledged without executing an Agent. Storage failure, exhausted capacity, or lease loss produces no success acknowledgement. Retries reuse Account-scoped provider event identities. For Slack and Lark, equivalent normalized events ignore receipt time and deduplicate across HTTP/socket retries; reused identities with different normalized content fail closed. This also recognizes retained HTTP receipts written before socket support.

Accounts selected for long connections establish the transport while reception is disabled, allowing setup before Agent activation. Administratively disabled or deleted accounts do not participate. Switching transport preserves Account IDs, targets, bindings, and memory. The lease migration adds an empty table without rewriting Accounts or backfilling existing data. Existing HTTP accounts remain compatible during rollout; enable WebSocket only after all control, connectivity, and worker replicas understand the extended provider schema. Before downgrading, return accounts to HTTP and stop connection owners. HTTP callbacks reject accounts currently configured for WebSocket delivery. Operators coordinate upstream switching; events lost by the platform during a switch are not recoverable by Service. Credential rotation and reconnection use bounded exponential backoff with jitter, capped at approximately one minute. Credential conflicts and failures remain observable and are retried; a configuration change causes reconciliation without waiting for that backoff.

## GitHub App HTTP v1

### Identity and credentials

`provider_key = "github"` and `provider_config_version = "github_app_http_v1"` select this profile. Immutable Account configuration contains exact operator-allowed `api_origin` and `web_origin`, GitHub App ID, installation ID, installation account ID, and a bot account database ID verified from the App installation. GitHub.com uses its official origins; GitHub Enterprise origins require an explicit Connectivity allowlist entry.

The GitHub Account owns write-only credential fields `webhook_secret` and `app_private_key_pem`. The private key signs RS256 App JWTs whose lifetime is at most ten minutes. The adapter exchanges them for installation access tokens, retains provider expiry, refreshes with safety skew through async single-flight, and never stores those tokens durably.

### Authentication and events

The raw body is limited to 8 MiB. Before JSON parsing the adapter requires `X-GitHub-Delivery`, `X-GitHub-Event`, and `X-Hub-Signature-256`, and verifies `sha256=` plus the lowercase hexadecimal HMAC-SHA256 of the exact body under `webhook_secret`. After parsing it verifies the exact installation, App target, installation account, and repository ownership facts available for the event. The delivery GUID is the durable external event identity; reuse with a different canonical body digest fails closed. Deduplication evidence is retained for seven days so bounded manual redelivery remains safe within Service's advertised horizon.

An authenticated `ping` is acknowledged without admission. Supported input event/action pairs are:

- `issues`: `opened`, `reopened`, `closed`, `labeled`, `unlabeled`, `assigned`, and `unassigned`;
- `issue_comment`: `created`;
- `pull_request`: `opened`, `reopened`, `closed`, `ready_for_review`, `converted_to_draft`, `synchronize`, `labeled`, and `unlabeled`;
- `pull_request_review`: `submitted` and `dismissed`; and
- `pull_request_review_comment`: `created`.

An exact target uses `target_kind="repository"` and one canonical positive decimal repository database ID. Repository names, labels, branches, and event content do not define match rules. Supported event/action pairs remain adapter-owned; the repository ID is immutable identity.

Safe normalization contains only the bounded event/action, repository ID and label, issue or pull-request number and kind, title/body projection, state, labels, verified actor projection, safe public URLs, and changed-field summary required by the event. Stable references are the installation plus repository database ID, that repository plus issue or pull-request number and kind, and the exact review thread/comment database or node ID when present.

Events authored by the verified bot account are ignored, as are unsupported edit/delete actions. Service does not append a visible or hidden operation marker to comments. The installation grants read-only Metadata plus only the repository permissions required by enabled events and actions: Issues read/write for issue input and comments, and Pull Requests read/write for pull-request input, reviews, comments, and file reads. An authenticated irrelevant, ping, or duplicate delivery returns `200`; a newly eligible event returns `202` only after durable admission. Invalid authentication returns `401`, malformed authenticated input returns `400`, and exhausted capacity returns `503` with `Retry-After`.

### Native actions

| Action key                | Model arguments             | Hidden current-context binding                      | Provider operation                |
| ------------------------- | --------------------------- | --------------------------------------------------- | --------------------------------- |
| `github.add_comment`      | bounded Markdown body       | current repository and issue or pull-request number | create issue comment              |
| `github.read_comments`    | page and bounded `per_page` | current issue or pull request                       | list issue comments               |
| `github.read_issue_or_pr` | finite include flags        | current issue or pull request                       | read issue or pull-request detail |
| `github.list_pr_files`    | page and bounded `per_page` | current pull request                                | list pull-request files           |

The model cannot provide an installation, owner, repository, issue, pull request, token, endpoint, or Account identifier. File patches have per-item and aggregate byte bounds and carry explicit truncation markers. A comment receipt includes only the provider comment database/node ID and safe public URL. Because this profile writes no correlation marker, a response lost after possible comment creation is `outcome_unknown` and is never retried automatically.

## Invariants

1. Every built-in event is authenticated over the exact bounded raw request before provider JSON becomes routing input.
2. A path ID, display name, sender identity, retry header, or provider URL grants no authority.
3. A challenge, ping, ignored bot event, unsupported event, or other deterministic non-input creates no admission.
4. Every activation-eligible event is durable before the adapter emits its success acknowledgement.
5. Current-context action destinations come only from protected inbound Run context. Proactive destinations are checked against the accepted Account target scope. Both resolve credentials only from their bound Account.
6. Slack channel chat is enabled only through explicit reception interaction mode and installed scopes; Lark discussion selection follows one fixed priority; GitHub v1 excludes Discussions.
7. No built-in write retries an unknown outcome without provider-owned reconciliation evidence.
