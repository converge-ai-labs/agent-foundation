import type { Schema } from "../../shared/api";

export function policyDraft(policy: Schema["OrganizationPolicy"]) {
  return {
    automatic: !!policy.enabled,
    model: policy.model ?? "",
    checkInterval: String(policy.check_interval_seconds ?? 10),
    cooldown: String(policy.cooldown_seconds ?? 300),
    trigger: policy.trigger?.type ?? "any_change",
    changeCount: String(
      policy.trigger?.type === "change_count" ? policy.trigger.count : 10,
    ),
    instructions: policy.instructions ?? "",
    history: policy.history ?? null,
  };
}
export type PolicyDraft = ReturnType<typeof policyDraft>;
export function policyValue(draft: PolicyDraft): Schema["OrganizationPolicy"] {
  return {
    enabled: draft.automatic,
    model: draft.model || null,
    check_interval_seconds: Number(draft.checkInterval),
    cooldown_seconds: Number(draft.cooldown),
    trigger:
      draft.trigger === "change_count"
        ? { type: "change_count", count: Number(draft.changeCount) }
        : { type: "any_change" },
    instructions: draft.instructions.trim() || null,
    history: draft.history,
  };
}
export function validInteger(value: string, min: number, max: number) {
  const number = Number(value);
  return (
    value.trim() !== "" &&
    Number.isInteger(number) &&
    number >= min &&
    number <= max
  );
}
export function validHistory(
  history: Schema["OrganizationHistory"] | null,
  automatic = false,
) {
  if (!history) return true;
  return (
    history.session_ids.length > 0 &&
    history.session_ids.length <= 100 &&
    new Set(history.session_ids).size === history.session_ids.length &&
    (!automatic || !!history.after) &&
    (!history.after || Number.isFinite(Date.parse(history.after))) &&
    (!history.before || Number.isFinite(Date.parse(history.before))) &&
    (!history.after ||
      !history.before ||
      Date.parse(history.after) <= Date.parse(history.before)) &&
    Number.isInteger(history.max_runs ?? 100) &&
    (history.max_runs ?? 100) >= 1 &&
    (history.max_runs ?? 100) <= 1000
  );
}
export function validPolicy(draft: PolicyDraft) {
  return (
    (!draft.automatic || !!draft.model) &&
    draft.instructions.trim().length <= 16384 &&
    validInteger(draft.checkInterval, 10, 86400) &&
    validInteger(draft.cooldown, 0, 604800) &&
    (draft.trigger !== "change_count" ||
      validInteger(draft.changeCount, 1, 1000000)) &&
    validHistory(draft.history, true)
  );
}
