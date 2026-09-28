import type { Schema } from "../transport/client";

export type EnvironmentSelection = Schema<"EnvironmentSelectionPatch">;

export function localAlias(index: number) {
  return index === 0 ? "workspace" : `workspace-${index + 1}`;
}

/** Keep a selected directory's identity when indexed local mount names change. */
export function changeLocalRoots(
  value: EnvironmentSelection,
  roots: string[],
): EnvironmentSelection {
  const previous = value.local_roots ?? [];
  const selected = previous.findIndex(
    (_, index) => localAlias(index) === value.default_environment,
  );
  if (selected < 0) return { local_roots: roots };
  const match = roots.indexOf(previous[selected]);
  const next = match < 0 && roots.length === previous.length ? selected : match;
  return {
    local_roots: roots,
    // An unresolved draft selection must never silently point at another directory.
    default_environment: next < 0 ? "workspace-0" : localAlias(next),
  };
}

export function invalidEnvironments(
  value: EnvironmentSelection,
  requireWorkspace = false,
) {
  const roots = value.local_roots ?? [];
  const bindings = value.environment_bindings ?? [];
  const choices = [
    "thread-files",
    ...roots.map((_, index) => localAlias(index)),
    ...bindings.map((item) => item.alias),
  ];
  return (
    roots.some((root) => !root.trim()) ||
    new Set(roots).size !== roots.length ||
    (!!value.default_environment &&
      !choices.includes(value.default_environment)) ||
    (bindings.length > 0 && !value.default_environment) ||
    (requireWorkspace && roots.length + bindings.length === 0)
  );
}
