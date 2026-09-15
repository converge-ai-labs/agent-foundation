import type { Schema } from "../transport/client";
import { readPreference, writePreference } from "../shell/preferences";

export type WizardDraft = {
  version: 1;
  step: number;
  connection: "codex" | "grok" | "api_key";
  selection: Schema<"SetupSelection">;
  apiProvider: string;
  modelId: string;
  baseUrl: string;
  sessionAffinityHeader?: string;
  preset: string;
  apiContext?: number;
  credential: string;
  threadId: string;
  pending?: {
    selection: Schema<"SetupSelection">;
    files: Record<string, string>;
  };
};
const key = (scope: string) => `setup.${scope}`;
export function readWizardDraft(scope: string): WizardDraft | undefined {
  try {
    const value = JSON.parse(readPreference(key(scope), "null"));
    if (
      value?.version === 1 &&
      ["codex", "grok", "api_key"].includes(value.connection) &&
      typeof value.selection === "object" &&
      value.selection &&
      typeof value.apiProvider === "string" &&
      typeof value.modelId === "string" &&
      typeof value.baseUrl === "string" &&
      (value.sessionAffinityHeader === undefined ||
        typeof value.sessionAffinityHeader === "string") &&
      typeof value.credential === "string" &&
      typeof value.preset === "string" &&
      /^thread-[a-f0-9]{32}$/.test(value.threadId) &&
      Number.isInteger(value.step) &&
      value.step >= 0 &&
      value.step <= 2
    )
      return value;
  } catch {
    /* A stale browser draft must not block discovery. */
  }
}
export function persistableBaseUrl(value: string) {
  if (!value) return true;
  try {
    const url = new URL(value);
    return (
      ["https:", "http:"].includes(url.protocol) &&
      !!url.hostname &&
      !url.username &&
      !url.password &&
      !url.search &&
      !url.hash &&
      !/\s/.test(value)
    );
  } catch {
    return false;
  }
}
export function saveWizardDraft(scope: string, draft: WizardDraft) {
  // Invalid endpoint input may contain pasted credentials. Retain that input in
  // memory only; the backend still owns endpoint validation before publication.
  const stored = {
    ...draft,
    baseUrl: persistableBaseUrl(draft.baseUrl) ? draft.baseUrl : "",
  };
  try {
    localStorage.setItem(
      `a13n-harness-ui.${key(scope)}`,
      JSON.stringify(stored),
    );
    return true;
  } catch {
    return false;
  }
}
export function finishWizard(scope: string) {
  writePreference(key(scope), "null");
  dismissWizard(scope);
}
export function dismissWizard(scope: string) {
  writePreference(`${key(scope)}.dismissed`, "true");
}
export function wizardDismissed(scope: string) {
  return readPreference(`${key(scope)}.dismissed`, "false") === "true";
}
