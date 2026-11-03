import type { AgentConfig } from "./configuration";

export type Subagent = NonNullable<AgentConfig["subagents"]>[string];
export type SubagentMode = NonNullable<AgentConfig["subagent_mode"]>;
export type SubagentLimits = NonNullable<Subagent["usage_limits"]>;

export function suggestedSubagentName(name: string, used: string[]) {
  const base =
    name
      .toLowerCase()
      .replace(/[^a-z0-9_]+/g, "_")
      .replace(/^\d+|^_+|_+$/g, "")
      .slice(0, 110) || "subagent";
  let next = base;
  for (let suffix = 2; used.includes(next); suffix++)
    next = `${base}_${suffix}`;
  return next;
}

export function subagentNameError(
  name: string,
  used: string[],
  previous?: string,
) {
  if (!/^[A-Za-z_][A-Za-z0-9_.:-]{0,127}$/.test(name))
    return "Start with a letter or underscore. Use up to 128 letters, digits, underscores, dots, colons or hyphens.";
  if (name !== previous && used.includes(name))
    return "This calling name is already in use.";
}

export function subagentLimitsError(
  mode: SubagentMode,
  limits: Subagent["usage_limits"],
) {
  if (!limits) return;
  if (
    mode === "async" &&
    Object.entries(limits).some(
      ([key, value]) =>
        key !== "request_limit" && value != null && value !== false,
    )
  )
    return "Async supports only a model request limit. Clear the other limits or choose Inline (sync).";
  if (
    Object.entries(limits).some(
      ([key, value]) =>
        key !== "count_tokens_before_request" &&
        value != null &&
        (!Number.isFinite(Number(value)) ||
          Number(value) < 0 ||
          (key !== "cost_limit" && !Number.isInteger(Number(value)))),
    )
  )
    return "Enter nonnegative limits. Request, tool call and token limits must be whole numbers.";
}
