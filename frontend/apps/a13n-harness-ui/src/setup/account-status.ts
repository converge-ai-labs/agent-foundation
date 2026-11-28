import type { Schema } from "../transport/client";

type Authentication = Pick<
  Schema<"AccountProjection">,
  "availability" | "usable" | "required_action"
>;
type UsageState = Pick<
  Schema<"UsageSnapshot">,
  "checked_at" | "error" | "required_action"
>;

const labels = {
  paused: "Paused",
  reauthenticate: "Reauthentication required",
  connected: "Connected",
  refresh: "Refreshes on first use",
  login: "Sign-in required",
  unsupported: "Storage unsupported",
} as const;
export type AccountStatusKind = keyof typeof labels;

export function accountStatus(
  authentication: Authentication | undefined,
  enabled = true,
  usage?: UsageState,
) {
  const reauthenticate = authentication?.required_action === "reauthenticate";
  let kind: AccountStatusKind;
  if (!enabled) kind = "paused";
  else if (reauthenticate) kind = "reauthenticate";
  else if (authentication?.usable) kind = "connected";
  else if (
    authentication?.availability === "available" &&
    authentication.required_action === "refresh"
  )
    kind = "refresh";
  else if (authentication?.required_action === "login") kind = "login";
  else if (
    authentication?.required_action === "switch_to_file" ||
    authentication?.availability === "unsupported"
  )
    kind = "unsupported";
  else kind = "login";

  const canRefresh = kind === "connected" || kind === "refresh";
  const refreshFailed = !!usage?.error && !reauthenticate;
  const stale =
    reauthenticate || refreshFailed || usage?.required_action === "refresh";
  const recovery =
    kind === "paused"
      ? "enable"
      : kind === "reauthenticate" || kind === "login"
        ? "signIn"
        : canRefresh && refreshFailed
          ? "refresh"
          : null;
  const tone =
    kind === "reauthenticate"
      ? "warning"
      : kind === "connected"
        ? "success"
        : "muted";
  return {
    kind,
    label: labels[kind],
    tone,
    reauthenticate,
    canRefresh,
    refreshFailed,
    stale,
    recovery,
  } as const;
}

export function subscriptionPollInterval(
  accounts:
    | readonly Pick<Schema<"SubscriptionAccountView">, "account" | "usage">[]
    | undefined,
) {
  const pending = accounts?.some(
    ({ account, usage }) =>
      account.enabled &&
      usage.required_action === "refresh" &&
      !usage.error &&
      usage.checked_at === 0,
  );
  return pending ? 2_000 : 15_000;
}
