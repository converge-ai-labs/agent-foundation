import type { Schema } from "../../shared/api";

export const computerActions = [
  "environment.computer.describe",
  "environment.computer.observe",
  "environment.computer.zoom",
  "environment.computer.click",
  "environment.computer.move",
  "environment.computer.drag",
  "environment.computer.scroll",
  "environment.computer.type_text",
  "environment.computer.press_keys",
] satisfies Schema["EnvironmentAction"][];
export const observationActions = [
  "environment.computer.describe",
  "environment.computer.observe",
  "environment.computer.zoom",
] satisfies Schema["EnvironmentAction"][];
export const computerPermissionLabels = {
  off: "Computer permissions: off",
  observe: "Computer permissions: allow observation",
  interact: "Computer permissions: allow interaction",
  custom: "Computer permissions: custom",
} as const;
type Mode = keyof typeof computerPermissionLabels;

export function computerPermissionMode(
  operations: Schema["EnvironmentAction"][],
): Mode {
  const selected = operations.filter((value) =>
    value.startsWith("environment.computer."),
  );
  if (!selected.length) return "off";
  if (
    selected.length === computerActions.length &&
    computerActions.every((value) => selected.includes(value))
  )
    return "interact";
  if (
    selected.length === observationActions.length &&
    observationActions.every((value) => selected.includes(value))
  )
    return "observe";
  return "custom";
}
