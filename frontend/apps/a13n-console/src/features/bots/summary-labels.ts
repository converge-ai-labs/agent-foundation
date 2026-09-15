import type { Schema } from "../../shared/api";

export type Condition = Schema["BotSummary"]["setup_condition"];
export const conditions: Record<Condition, string> = {
  disabled: "Disabled",
  needs_verification: "Needs verification",
  check_failed: "Verification failed",
  reception_off: "Reception off",
  receiving: "Reception enabled",
};
export const stages: Record<
  NonNullable<Schema["BotSummary"]["test_stage"]>,
  string
> = {
  waiting: "Waiting for test message",
  expired: "Test expired",
  stale: "Test configuration changed",
  received: "Test event received",
  rejected: "Test message rejected",
  accepted: "Test accepted · reply unconfirmed",
  reply_confirmed: "Test reply confirmed",
};
