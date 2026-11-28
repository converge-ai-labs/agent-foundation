import { expect, it } from "vitest";
import { accountStatus, subscriptionPollInterval } from "./account-status";
import type { Schema } from "../transport/client";

const connected = { availability: "available", usable: true } as const;
const revoked = { ...connected, required_action: "reauthenticate" } as const;
const rejectedSnapshot = { error: "Refresh grant was rejected" };

it("requires sign-in before a revoked grant can refresh even if its local token looks usable", () => {
  expect(accountStatus(revoked, true, rejectedSnapshot)).toMatchObject({
    kind: "reauthenticate",
    tone: "warning",
    canRefresh: false,
    stale: true,
    refreshFailed: false,
    recovery: "signIn",
  });
  expect(accountStatus(revoked, false, rejectedSnapshot)).toMatchObject({
    kind: "paused",
    tone: "muted",
    canRefresh: false,
    recovery: "enable",
  });
});

it("offers usage retry only when the connection can refresh", () => {
  const failedUsage = {
    required_action: "refresh",
    error: "Unavailable",
  } as const;
  expect(accountStatus(connected, true, failedUsage)).toMatchObject({
    kind: "connected",
    canRefresh: true,
    stale: true,
    recovery: "refresh",
  });
  expect(
    accountStatus({
      availability: "unsupported",
      usable: false,
      required_action: "login",
    }),
  ).toMatchObject({
    kind: "login",
    canRefresh: false,
    recovery: "signIn",
  });
  const refreshable = {
    availability: "available",
    usable: false,
    required_action: "refresh",
  } as const;
  expect(accountStatus(refreshable)).toMatchObject({
    kind: "refresh",
    canRefresh: true,
    recovery: null,
  });
  expect(
    accountStatus(
      { ...refreshable, availability: "absent" },
      true,
      failedUsage,
    ),
  ).toMatchObject({ kind: "login", canRefresh: false, recovery: "signIn" });
  expect(
    accountStatus({
      ...refreshable,
      availability: "unsupported",
      required_action: "switch_to_file",
    }),
  ).toMatchObject({ kind: "unsupported", canRefresh: false, recovery: null });
});

it("reads the local cache faster only while approved sign-in is awaiting usage", () => {
  const account = {
    id: "acct-codex-default",
    name: "Codex",
    provider: "codex",
    enabled: true,
  };
  const usage = { required_action: "refresh", checked_at: 0 } as const;
  expect(subscriptionPollInterval([{ account, usage }])).toBe(2_000);
  const settled: Pick<
    Schema<"SubscriptionAccountView">,
    "account" | "usage"
  >[] = [
    { account: { ...account, enabled: false }, usage },
    { account, usage: { ...usage, checked_at: 1 } },
    { account, usage: { ...usage, error: "Failed" } },
    { account, usage: { checked_at: 1 } },
  ];
  expect(subscriptionPollInterval(settled)).toBe(15_000);
  expect(subscriptionPollInterval(undefined)).toBe(15_000);
});
