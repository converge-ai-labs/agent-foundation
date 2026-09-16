import type { Schema } from "../../shared/api";

/** Bot-facing projection of installation identity and separately versioned settings. */
export type BotAccount = Schema["Account"] & {
  memory: Schema["AccountMemorySettings"]["memory"];
  memoryVersion: number;
};

export function botAccount(summary: Schema["BotSummary"]): BotAccount {
  return {
    ...summary.account,
    memory: summary.memory_settings.memory,
    memoryVersion: summary.memory_settings.version,
  };
}
