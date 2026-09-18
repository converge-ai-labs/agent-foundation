# Messaging Reception

## Design Position

A messaging [Account](01a-application-accounts.md) represents one installed App or Bot identity. Reception chooses one Agent through Account defaults or an exact channel/chat target. Conversation and Discussion are provider concepts; Service Thread remains the durable history identity. General Connector tools are independent of native Bot reception and replies.

## Interaction Policy

Account and exact target `provider_policy` embed the provider-validated messaging policy:

```python
class MessagingPolicy:
    interaction_mode: Literal["mention", "discussion", "chat"]
    reply_mode: Literal["auto", "thread", "main"]
```

| Mode         | Group activation                                                                                   | Correlation                 |
| ------------ | -------------------------------------------------------------------------------------------------- | --------------------------- |
| `mention`    | Every admitted message requires an authenticated App mention                                       | Provider-native Discussion  |
| `discussion` | An App mention starts an unbound Discussion; an established Discussion continues without a mention | Provider-native Discussion  |
| `chat`       | Every supported human message in the exact Conversation is admitted                                | Whole provider Conversation |

A direct message is addressed by construction. In `mention` and `discussion`, its provider container supplies the stable Discussion unless the provider supplies a narrower continuation unit. `chat` includes supported subthread messages in the Conversation reference. Providers expose only modes their permissions and event transport support and reject unsupported policy explicitly.

Authenticated App mentions are activation controls. The safe input text excludes the App mention; all other tokens remain ordinary input. A mention-only message without supported text or attachment content is filtered before admission and creates no Binding or Run. Own/Bot events and unsupported system traffic follow the adapter's loop-prevention rules.

## Agent and Thread Resolution

The provider policy selects an exact Discussion or Conversation reference. The [Binding](01-ingress-and-routing.md#binding-event-and-batch-authority) preserves its Service Thread. A new ordinary Run uses the current target Agent or Account default and the target's narrow canonical override. An active or selected waiting Run receives Steer with its existing configuration. Content cannot select an Agent, and configuration changes never move existing history to another Thread.

A top-level mention can establish correlation to its future provider thread root before the Bot replies. In `discussion`, a reservation with no accepted Thread does not authorize mention-free continuation. In `chat`, complete listening does not require the Agent to reply; it may remain silent.

The shared [batching contract](01-ingress-and-routing.md#input-batching-and-frequency) preserves bounded ordered bursts and limits Run/Steer frequency. Messaging has no extra attention gate, command grammar, Agent handoff protocol, or parallel input queue.

## Outbound Boundary

Reception does not generate an Agent answer automatically. Bot-owned task progress notices are a separate, fixed status projection described in [Bots](../../frontend/bots.md#task-progress-and-control). The Agent calls an authorized native action through the [Run-bound tool context](04-agent-facing-tools.md). Current-conversation actions expose no model-selected destination, Account, credential, or Run ID.

`thread` and `main` force provider-native placement and omit a placement argument. `auto` permits a bounded provider choice: top-level group tasks normally use an available thread/topic/reply chain, while direct messages remain in their main flow. Placement cannot change the Binding. A main-flow notification does not migrate the current Discussion.

Closing Account or target reception blocks new admission; already acknowledged batches continue processing while accepted replies retain their bounded context. Account disablement or lost execution authority blocks subsequent dispatch.

Feishu/Lark and Slack Bot task messages combine status projection with explicitly authorized native replies. `lark.reply` and `slack.reply` may update the existing task card through the Bot contribution; it must preserve fresh Attempt/source authority and provider-confirmed reply observations. Raw model completion is never a reply trigger.
