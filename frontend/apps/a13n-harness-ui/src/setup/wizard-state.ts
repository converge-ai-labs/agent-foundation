import type { ModelEditorDraft } from "../configuration/model-editor";
import type { Schema } from "../transport/client";
import { readPreference, writePreference } from "../shell/preferences";

export type WizardDraft = {
  version: 2;
  step: number;
  selection: Schema<"SetupSelection">;
  threadId: string;
  modelDraft?: ModelEditorDraft;
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
      value?.version === 2 &&
      typeof value.selection === "object" &&
      value.selection &&
      /^thread[-_][a-f0-9]{32}$/.test(value.threadId) &&
      Number.isInteger(value.step) &&
      value.step >= 0 &&
      value.step <= 1
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
  // Only prepared recipes enter this draft. Never persist invalid endpoint input,
  // and never sanitize an exact pending publication into a different intent.
  for (const selection of [draft.selection, draft.pending?.selection]) {
    const url = selection?.model?.model_configuration?.base_url;
    if (
      url !== undefined &&
      (typeof url !== "string" || !persistableBaseUrl(url))
    )
      return false;
  }
  try {
    const stored = {
      ...draft,
      modelDraft: draft.modelDraft
        ? {
            ...draft.modelDraft,
            baseUrl: persistableBaseUrl(draft.modelDraft.baseUrl)
              ? draft.modelDraft.baseUrl
              : "",
          }
        : undefined,
    };
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
