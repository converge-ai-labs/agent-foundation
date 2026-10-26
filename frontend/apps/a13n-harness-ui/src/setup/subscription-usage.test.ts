import { expect, it } from "vitest";
import {
  availableVouchers,
  voucherSummary,
  windowLabel,
} from "./subscription-usage";
import type { Schema } from "../transport/client";

it("names actual window durations rather than assuming primary and secondary meanings", () => {
  expect([18000, 604800, 86400, 7200, 90].map(windowLabel)).toEqual([
    "5h",
    "Weekly",
    "Daily",
    "2h",
    "90s",
  ]);
});

it("counts only redeemable vouchers and finds the earliest known future expiry", () => {
  const credits = {
    available_count: 5,
    credits: [
      {
        id: "later",
        status: "available",
        reset_type: "codex_rate_limits",
        expires_at: "2030-02-02T00:00:00Z",
      },
      {
        id: "first",
        status: "available",
        reset_type: "codex_rate_limits",
        expires_at: "2030-02-01T00:00:00Z",
      },
      { id: "undated", status: "available", reset_type: "codex_rate_limits" },
      {
        id: "expired",
        status: "available",
        reset_type: "codex_rate_limits",
        expires_at: "2020-01-01T00:00:00Z",
      },
      { id: "used", status: "redeemed", reset_type: "codex_rate_limits" },
      { id: "other", status: "available", reset_type: "other" },
    ],
  };
  const now = Date.parse("2030-01-01T00:00:00Z");
  expect(availableVouchers(credits, now).map((item) => item.id)).toEqual([
    "first",
    "later",
    "undated",
  ]);
  expect(credits.credits[0].id).toBe("later");
  expect(
    voucherSummary(
      { account_id: "a", plan_type: "plus", reset_credits: credits },
      now,
    ),
  ).toEqual({ count: 3, expiresAt: Date.parse("2030-02-01T00:00:00Z") });
});

it("preserves the provider count when voucher details fail, and distinguishes unknown from zero", () => {
  const usage: Schema<"CodexUsage"> = {
    account_id: "a",
    plan_type: "plus",
    rate_limit_reset_credits: { available_count: 2 },
    reset_unavailable: "Unavailable",
  };
  expect(voucherSummary(usage)).toEqual({ count: 2, expiresAt: null });
  expect(voucherSummary(null)).toEqual({ count: null, expiresAt: null });
  expect(
    voucherSummary({
      ...usage,
      reset_credits: { available_count: 0, credits: [] },
    }),
  ).toEqual({ count: 0, expiresAt: null });
});

it("uses Grok's modern percentage before legacy money and preserves unknown periods", async () => {
  const { grokAllowance } = await import("./subscription-usage");
  expect(
    grokAllowance({
      credit_usage_percent: 42.5,
      monthly_limit: { val: 1000 },
      used: { val: 900 },
      current_period: {
        type: "USAGE_PERIOD_TYPE_WEEKLY",
        end: "2030-02-01T00:00:00Z",
      },
    }),
  ).toEqual({
    usedPercent: 42.5,
    label: "Weekly",
    resetAt: "2030-02-01T00:00:00Z",
  });
  expect(
    grokAllowance({
      monthly_limit: { val: 2000 },
      used: { val: 500 },
      billing_period_end: "2030-01-31T00:00:00Z",
    }).usedPercent,
  ).toBe(25);
  expect(grokAllowance(null)).toEqual({
    usedPercent: null,
    label: "Current period",
    resetAt: null,
  });
  expect(grokAllowance({ credit_usage_percent: 140 }).usedPercent).toBe(100);
});
