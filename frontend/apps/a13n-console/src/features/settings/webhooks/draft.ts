import type { Schema } from "../../../shared/api";
import type { Subscription } from "./api";

type Kind = Schema["LifecycleKind"];

/** Labels describe the transition; wire event names stay visible beside them. */
export const lifecycleLabels = {
  "run.accepted": "Accepted",
  "run.running": "Running",
  "run.waiting": "Waiting for action",
  "run.completed": "Completed",
  "run.failed": "Failed",
  "run.cancelled": "Cancelled",
  "run_attempt.leased": "Leased",
  "run_attempt.running": "Running",
  "run_attempt.succeeded": "Succeeded",
  "run_attempt.yielded": "Yielded",
  "run_attempt.failed": "Failed",
  "run_attempt.cancelled": "Cancelled",
} satisfies Record<Kind, string>;

export const lifecycleKinds = Object.keys(lifecycleLabels) as Kind[];
export const outcomeKinds: Kind[] = [
  "run.waiting",
  "run.completed",
  "run.failed",
  "run.cancelled",
];

export interface SubscriptionDraft {
  name: string;
  url: string;
  kinds: Kind[];
  agentId: string;
  sessionId: string;
  threadId: string;
  enabled: boolean;
  signingSecret: string;
}

export function subscriptionDraft(value?: Subscription): SubscriptionDraft {
  return {
    name: value?.name ?? "",
    url: value?.url ?? "",
    // Keep saved kinds even if a newer Service supplied a kind this Console does not label yet.
    kinds: value ? ([...value.kinds] as Kind[]) : [...outcomeKinds],
    agentId: value?.filter.agent_id ?? "",
    sessionId: value?.filter.session_id ?? "",
    threadId: value?.filter.thread_id ?? "",
    enabled: value?.enabled ?? true,
    signingSecret: "",
  };
}

export function subscriptionBody(
  draft: SubscriptionDraft,
): Schema["SubscriptionCreate"] {
  return {
    name: draft.name.trim(),
    url: draft.url.trim(),
    kinds: draft.kinds,
    filter: {
      ...(draft.agentId.trim() ? { agent_id: draft.agentId.trim() } : {}),
      ...(draft.sessionId.trim() ? { session_id: draft.sessionId.trim() } : {}),
      ...(draft.threadId.trim() ? { thread_id: draft.threadId.trim() } : {}),
    },
    enabled: draft.enabled,
    // A blank field must never replace an existing secret.
    ...(draft.signingSecret ? { signing_secret: draft.signingSecret } : {}),
  };
}

/** Only the fields edited in this draft are sent on update. */
export function subscriptionPatch(
  draft: SubscriptionDraft,
  basis: Subscription,
): Schema["SubscriptionUpdate"] {
  const body = subscriptionBody(draft);
  const original = subscriptionBody(subscriptionDraft(basis));
  return Object.fromEntries(
    Object.entries(body).filter(
      ([key, value]) =>
        JSON.stringify(value) !==
        JSON.stringify(original[key as keyof typeof original]),
    ),
  );
}

/** Carry the user's changed fields onto a fresh version without replacing other people's edits. */
export function rebaseDraft(
  draft: SubscriptionDraft,
  basis: Subscription,
  latest: Subscription,
): SubscriptionDraft {
  const original = subscriptionDraft(basis);
  const updated = subscriptionDraft(latest);
  return Object.fromEntries(
    Object.entries(draft).map(([key, value]) => [
      key,
      JSON.stringify(value) !==
      JSON.stringify(original[key as keyof SubscriptionDraft])
        ? value
        : updated[key as keyof SubscriptionDraft],
    ]),
  ) as unknown as SubscriptionDraft;
}

export function canRedeliver(delivery: {
  status: string;
  last_error: string | null;
}) {
  return (
    delivery.status === "dead" &&
    delivery.last_error !== "signing_secret_unavailable"
  );
}
