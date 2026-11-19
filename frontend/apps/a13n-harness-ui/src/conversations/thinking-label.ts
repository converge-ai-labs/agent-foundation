import type { TFunction } from "i18next";
import type { Schema } from "../transport/client";
import { modelControlMessage } from "./model-control-messages";

type ThinkingControl = Schema<"ModelSummary">["thinking"];
type ThinkingSelection = Schema<"SubmitRequest">["thinking"];

export function thinkingLevelLabel(
  control: ThinkingControl,
  selection: ThinkingSelection,
  t: TFunction,
) {
  const value = selection ?? control?.default_value;
  const option =
    value == null
      ? undefined
      : control?.options.find((item) => item.value === value);
  if (option) return modelControlMessage(option.label, t);
  return selection == null ? undefined : t("Unavailable selection");
}

export function thinkingDetailLabel(
  control: ThinkingControl,
  selection: ThinkingSelection,
  t: TFunction,
) {
  const level = thinkingLevelLabel(control, selection, t);
  if (level === undefined)
    return modelControlMessage(control?.default_summary ?? "Default", t);
  return selection == null ? t("{{level}} (default)", { level }) : level;
}
