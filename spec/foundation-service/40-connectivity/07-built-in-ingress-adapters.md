# Built-in Ingress Adapters

## Design Position

The first built-in HTTP adapters are Slack, Lark/Feishu, and GitHub App. Each owns one exact installation identity, wire authentication profile, event vocabulary, Route match type, safe event projection, correlation references, acknowledgement behavior, and native action set. They reuse the common [Ingress and durable admission](01-ingress-and-routing.md) kernel without sharing a provider payload model.

Only HTTP webhook delivery is supported in this profile. Slack Socket Mode, Lark WebSocket delivery, repository webhooks authenticated by a shared secret plus PAT, and GitHub Discussions are separate future transport or provider contracts.

All adapters reject unknown configuration versions and request bodies beyond their limit before JSON parsing. They compare authentication values in constant time, suppress their verified bot identity, and never place raw bodies, message content, signatures, tokens, private keys, callback handles, or provider errors in logs, traces, metrics labels, audit details, or public errors.

## Slack HTTP v1

### Identity and credentials

`provider_key = "slack"` and `provider_config_version = "slack_http_v1"` select this profile. The immutable provider configuration contains `api_app_id`, exactly one installed `team_id`, optional `enterprise_id`, verified `bot_user_id`, and `events_transport = "http"`. A changed App, installation, enterprise scope, or bot identity creates another Ingress.

The Ingress owns write-only Secrets `signing_secret` and `bot_token`. The signing Secret authenticates webhooks. The bot token is used only for the enabled native Slack operations against `https://slack.com`; it never authenticates inbound delivery.

### Authentication and events

The raw body is limited to 1 MiB. Before parsing JSON, the adapter requires `X-Slack-Request-Timestamp` and `X-Slack-Signature`, rejects a timestamp more than five minutes from current UTC, and verifies `v0=` plus the lowercase hexadecimal HMAC-SHA256 of `v0:<timestamp>:<raw-body>` under `signing_secret`. After parsing, it verifies the configured `api_app_id`, team, enterprise scope, and event authorization against the concrete installation.

An authenticated `url_verification` request returns its exact bounded challenge and creates no admission. An authenticated `event_callback` uses `event_id` as its durable external event identity. Retry number and reason headers are bounded observations and do not change identity. Reuse of one retained `event_id` with a different canonical body digest fails closed.

The supported input events are:

- `app_mention`;
- `message` in `im` conversations; and
- `message` in public channels, private channels, or multi-person direct messages only when the Ingress installation has the required event subscription and the selected Route interaction mode permits `chat` or `discussion`.

The adapter ignores its verified `bot_user_id`, messages with a `bot_id`, the `bot_message` subtype, edited or deleted message subtypes, message bodies containing no content after the exact App mention is removed, and every unsupported event or subtype. Ignored events are acknowledged without admission.

Safe normalization exposes bounded user and conversation IDs, display labels when already present, conversation kind, timestamp, thread relationship, mention facts, and text with the App mention removed. It does not fetch profile or history data during receipt. Stable references are:

- Conversation: `<team_id>:<channel_id>`;
- Discussion: `<team_id>:<channel_id>:<root_thread_ts>`, where a top-level activation uses its own message `ts` as the root;
- Message: `<team_id>:<channel_id>:<message_ts>`.

`SlackRouteMatch` contains finite event kinds, finite conversation kinds, and an optional finite set of exact channel IDs. Two enabled Routes overlap when their event and conversation-kind sets intersect and either channel set is absent or the channel sets intersect. The default mapping produces canonical message input from safe text, actor, conversation, and discussion context.

An irrelevant, challenge, or already-known delivery returns HTTP `200`. A newly eligible event returns HTTP `200` only after durable admission. Invalid authentication returns `401`, malformed or unsupported authenticated input returns `400` when it cannot be ignored safely, and unavailable durable capacity returns `503` with `Retry-After`. The request handler performs no Slack Web API call and returns within Slack's three-second acknowledgement window under healthy local dependencies. Deduplication evidence is retained for 24 hours.

### Native actions

The installation requests only scopes required by enabled events and actions. These can include `app_mentions:read`, the matching `channels:history`, `groups:history`, `im:history`, or `mpim:history` scopes, `chat:write`, the corresponding conversation read scopes, and `users:read`. Disabling an action removes it from later Run selections even if the token still has its scope.

| Action key            | Model arguments                                                                         | Hidden current-context binding    | Provider operation                                                   |
| --------------------- | --------------------------------------------------------------------------------------- | --------------------------------- | -------------------------------------------------------------------- |
| `slack.reply`         | bounded `text`; bounded placement enum only when Route policy is `auto`                 | channel and root thread timestamp | `chat.postMessage`                                                   |
| `slack.list_members`  | `limit` from 1 through 100 and opaque cursor                                            | current channel                   | `conversations.members`, with bounded cached `users.info` enrichment |
| `slack.read_messages` | `scope` in `conversation` or `discussion`, `limit` from 1 through 15, and opaque cursor | current channel and thread        | the applicable conversations history or replies operation            |

The model cannot provide a team, channel, thread, message, token, or Ingress identifier. Forced `conversation` or `discussion` reply policy omits placement from model arguments. A reply receipt contains only the returned channel, message timestamp, root thread timestamp, and safe request correlation. A lost response after possible write dispatch is `outcome_unknown` and is not retried automatically. Reads surface bounded rate-limit evidence and do not sleep through a long `Retry-After` inside one tool call.

## Lark/Feishu HTTP v1

### Identity and credentials

`provider_key = "lark"` and `provider_config_version = "lark_http_v1"` select both brands. Immutable configuration contains `brand` in `feishu` or `lark`, an official or exact operator-allowed `open_api_origin`, `app_id`, installed `tenant_key`, verified `bot_open_id`, and `events_transport = "http"`. Brand and origin are explicit configuration rather than separate provider keys.

The Ingress owns write-only Secrets `app_secret`, `encrypt_key`, and `verification_token`. `encrypt_key` can be absent only for an installation deliberately configured without encrypted event delivery. A short-lived tenant access token is derived from `app_id` and `app_secret`, refreshed before provider expiry through async single-flight, and retained only in process memory or the shared ephemeral cache; it is not another durable Secret.

### Authentication, decryption, and events

The raw body is limited to 1 MiB. When `encrypt_key` is configured, the adapter requires the timestamp, nonce, and signature headers, rejects a timestamp more than five minutes from current UTC, and verifies the lowercase SHA-256 digest of the exact concatenation `<timestamp><nonce><encrypt_key><raw-body>`. For an encrypted envelope it base64-decodes `encrypt`, derives the AES-256 key as SHA-256 of the UTF-8 encryption key, uses the first 16 ciphertext bytes as the IV, decrypts AES-CBC, and strictly validates PKCS#7 padding before parsing the inner JSON.

The adapter then verifies the v2 event header token, `app_id`, and `tenant_key` against the Ingress. URL verification returns the exact bounded challenge only after those checks and creates no admission. `header.event_id` is the durable external event identity; reuse with different canonical payload evidence fails closed. Deduplication evidence is retained for 24 hours.

The supported input event is `im.message.receive_v1`. The installation enables only the application permissions needed by configured behavior: message receive/read, send-as-bot, and chat-member read. Safe normalization accepts text and post messages as bounded text projections. Image, file, audio, video, and other message types expose only bounded type and attachment metadata and cannot materialize binary content. The view can include bounded sender ID and kind, message ID, root, parent and thread IDs, chat ID and type, exact mentions, message type, text, and provider creation time.

Stable references are:

- Conversation: `<tenant_key>:<chat_id>`;
- Discussion: `thread_id` when present, otherwise `root_id`, otherwise `message_id` for an activating group message or `chat_id` for a direct message;
- Message: `<tenant_key>:<message_id>`.

The adapter removes only mentions whose provider identity exactly matches `bot_open_id`, ignores mention-only input and messages sent by that bot, and never equates a sender email or display name with a Foundation Principal. `LarkRouteMatch` contains finite event kinds, finite chat types, and an optional finite set of exact chat IDs. Overlap is the intersection of those three finite scopes. The default mapping produces canonical message input from the safe post/text, actor, chat, and discussion projection.

An irrelevant or duplicate delivery returns HTTP `200` with the bounded success body `{"codemsg":"success"}`. URL verification returns HTTP `200` with only `{"challenge":"<exact challenge>"}`. A newly eligible event returns the success acknowledgement only after durable admission. Invalid signatures, encryption, token, App, or tenant identity fail without admission; malformed authenticated input fails with a bounded error; exhausted durable capacity returns `503` with `Retry-After`. The receipt handler performs no Open API call. Provider timestamp is retained as ordering evidence after the independent five-minute request freshness check.

### Native actions

| Action key           | Model arguments                                                             | Hidden current-context binding       | Provider operation                                         |
| -------------------- | --------------------------------------------------------------------------- | ------------------------------------ | ---------------------------------------------------------- |
| `lark.reply`         | bounded text or post content; bounded placement enum only for `auto` policy | message, chat, and discussion policy | reply to the current message or create in the current chat |
| `lark.list_members`  | bounded limit and opaque page token                                         | current chat                         | list current chat members                                  |
| `lark.read_messages` | bounded scope, time/order options, and opaque page token                    | current chat or discussion           | list messages for the bound container                      |

The model cannot provide an App, tenant, chat, thread, message, token, or Ingress identifier. Write calls carry a stable provider UUID derived from the tool effect identity. Receipts expose only bounded message or member projections and provider message identity. A provider response lost after possible dispatch is reconciled only through that UUID or authoritative provider evidence; otherwise the result is `outcome_unknown`.

## GitHub App HTTP v1

### Identity and credentials

`provider_key = "github"` and `provider_config_version = "github_app_http_v1"` select this profile. Immutable configuration contains exact operator-allowed `api_origin` and `web_origin`, GitHub App ID, installation ID, installation account ID, and a bot account database ID verified from the App installation. GitHub.com uses its official origins; GitHub Enterprise origins require an explicit Connectivity allowlist entry.

The Ingress owns write-only Secrets `webhook_secret` and `app_private_key_pem`. The private key signs RS256 App JWTs whose lifetime is at most ten minutes. The adapter exchanges them for installation access tokens, retains provider expiry, refreshes with safety skew through async single-flight, and never stores those tokens durably.

### Authentication and events

The raw body is limited to 8 MiB. Before JSON parsing the adapter requires `X-GitHub-Delivery`, `X-GitHub-Event`, and `X-Hub-Signature-256`, and verifies `sha256=` plus the lowercase hexadecimal HMAC-SHA256 of the exact body under `webhook_secret`. After parsing it verifies the exact installation, App target, installation account, and repository ownership facts available for the event. The delivery GUID is the durable external event identity; reuse with a different canonical body digest fails closed. Deduplication evidence is retained for seven days so bounded manual redelivery remains safe within Foundation's advertised horizon.

An authenticated `ping` is acknowledged without admission. Supported input event/action pairs are:

- `issues`: `opened`, `reopened`, `closed`, `labeled`, `unlabeled`, `assigned`, and `unassigned`;
- `issue_comment`: `created`;
- `pull_request`: `opened`, `reopened`, `closed`, `ready_for_review`, `converted_to_draft`, `synchronize`, `labeled`, and `unlabeled`;
- `pull_request_review`: `submitted` and `dismissed`; and
- `pull_request_review_comment`: `created`.

Route configuration explicitly selects finite event/action pairs, exact immutable repository database IDs, optional exact label names, and optional exact base branches. Two enabled Routes overlap when their repository and event/action sets intersect and their optional predicates admit a common event. Repository owner or name is display metadata and never substitutes for the database ID.

Safe normalization contains only the bounded event/action, repository ID and label, issue or pull-request number and kind, title/body projection, state, labels, verified actor projection, safe public URLs, and changed-field summary required by the event. Stable references are the installation plus repository database ID, that repository plus issue or pull-request number and kind, and the exact review thread/comment database or node ID when present.

Events authored by the verified bot account are ignored, as are unsupported edit/delete actions. Foundation does not append a visible or hidden operation marker to comments. The installation grants read-only Metadata plus only the repository permissions required by enabled events and actions: Issues read/write for issue input and comments, and Pull Requests read/write for pull-request input, reviews, comments, and file reads. An authenticated irrelevant, ping, or duplicate delivery returns `200`; a newly eligible event returns `202` only after durable admission. Invalid authentication returns `401`, malformed authenticated input returns `400`, and exhausted capacity returns `503` with `Retry-After`.

### Native actions

| Action key                | Model arguments             | Hidden current-context binding                      | Provider operation                |
| ------------------------- | --------------------------- | --------------------------------------------------- | --------------------------------- |
| `github.add_comment`      | bounded Markdown body       | current repository and issue or pull-request number | create issue comment              |
| `github.read_comments`    | page and bounded `per_page` | current issue or pull request                       | list issue comments               |
| `github.read_issue_or_pr` | finite include flags        | current issue or pull request                       | read issue or pull-request detail |
| `github.list_pr_files`    | page and bounded `per_page` | current pull request                                | list pull-request files           |

The model cannot provide an installation, owner, repository, issue, pull request, token, endpoint, or Ingress identifier. File patches have per-item and aggregate byte bounds and carry explicit truncation markers. A comment receipt includes only the provider comment database/node ID and safe public URL. Because this profile writes no correlation marker, a response lost after possible comment creation is `outcome_unknown` and is never retried automatically.

## Invariants

1. Every built-in event is authenticated over the exact bounded raw request before provider JSON becomes routing input.
2. A path ID, display name, sender identity, retry header, or provider URL grants no authority.
3. A challenge, ping, ignored bot event, unsupported event, or other deterministic non-input creates no admission.
4. Every activation-eligible event is durable before the adapter emits its success acknowledgement.
5. Native action destination and credential identities come only from the protected current Run context.
6. Slack channel chat is enabled only through explicit Route interaction mode and installed scopes; Lark discussion selection follows one fixed priority; GitHub v1 excludes Discussions.
7. No built-in write retries an unknown outcome without provider-owned reconciliation evidence.
