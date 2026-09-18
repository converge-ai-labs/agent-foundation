import { BrandIcon, StatusPill } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { StatePill } from "../../shared/feedback";

export type Platform = "slack" | "lark" | "github";

export const platforms: readonly Platform[] = ["slack", "lark", "github"];

export function isPlatform(value: string): value is Platform {
  return (platforms as readonly string[]).includes(value);
}

/**
 * One place decides how a platform is named. Slack and GitHub keep their own
 * spelling in every language; Feishu is the translated name of `lark`.
 */
export function usePlatformName() {
  const { t } = useTranslation();
  return (key: string) =>
    key === "slack"
      ? "Slack"
      : key === "github"
        ? "GitHub"
        : key === "lark"
          ? t("Feishu")
          : key;
}

/** Brand mark for a platform, sized for a tile or an inline label. */
export function PlatformIcon({
  type,
  size = 20,
}: {
  type: string;
  size?: number;
}) {
  return <BrandIcon alias={type === "lark" ? "feishu" : type} size={size} />;
}

/** Reception is a plain enabled / disabled state wherever it appears. */
export function ReceptionPill({ enabled }: { enabled: boolean }) {
  return <StatePill state={enabled ? "enabled" : "disabled"} />;
}

/** Bot setup conditions, from the summary endpoint. */
export const setupConditions = {
  disabled: "Disabled",
  needs_verification: "Needs verification",
  check_failed: "Verification failed",
  reception_off: "Message responses off",
  receiving: "Message responses enabled",
} as const;

export type SetupCondition = keyof typeof setupConditions;

const setupVariants = {
  disabled: "neutral",
  needs_verification: "warning",
  check_failed: "danger",
  reception_off: "neutral",
  receiving: "success",
} as const;

/** The bot's own progress through setup, quieter than a failure. */
export function SetupPill({ condition }: { condition: SetupCondition }) {
  const { t } = useTranslation();
  return (
    <StatusPill variant={setupVariants[condition]} data-state={condition}>
      {t(setupConditions[condition])}
    </StatusPill>
  );
}

/** Stages of the guided setup test. */
export const testStages = {
  waiting: "Waiting for test message",
  expired: "Test expired",
  stale: "Test configuration changed",
  received: "Test event received",
  rejected: "Test message rejected",
  accepted: "Test accepted · reply unconfirmed",
  reply_confirmed: "Test reply confirmed",
} as const;

export type TestStage = keyof typeof testStages;

/** Platform reply attempts, named the same way everywhere. */
export const replyStatuses = {
  dispatching: "Awaiting reply confirmation",
  succeeded: "Provider confirmed reply",
  rejected: "Reply rejected",
  outcome_unknown: "Reply outcome unknown",
} as const;

export type ReplyStatus = keyof typeof replyStatuses;

export function ReplyPill({ status }: { status: ReplyStatus }) {
  const { t } = useTranslation();
  return <StatePill state={status} label={t(replyStatuses[status])} />;
}
