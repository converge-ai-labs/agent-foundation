import type { Schema } from "../transport/client";

export type EnvironmentSelection = Schema<"EnvironmentSelectionPatch">;
export type EnvironmentBinding = Schema<"EnvironmentBindingSelection">;

/** A Device and execution policy own a group of working directories, not vice versa. */
export type DeviceEnvironment = {
  key: string;
  deviceId: string;
  directories: EnvironmentBinding[];
};

function policyKey(binding: EnvironmentBinding) {
  return JSON.stringify(
    {
      device: binding.device_id,
      permissions: binding.permission_ceiling
        ? [...(binding.permission_ceiling.operations ?? [])].sort()
        : null,
      egress: binding.egress ?? null,
      boundary: binding.expected_boundary ?? null,
    },
    (_, value) =>
      value && typeof value === "object" && !Array.isArray(value)
        ? Object.fromEntries(
            Object.entries(value).sort(([a], [b]) => a.localeCompare(b)),
          )
        : value,
  );
}

/** Group without rewriting stored order, aliases, or policy values. */
export function deviceEnvironments(bindings: readonly EnvironmentBinding[]) {
  const groups = new Map<string, DeviceEnvironment>();
  for (const binding of bindings) {
    const key = policyKey(binding);
    const group = groups.get(key);
    if (group) group.directories.push(binding);
    else
      groups.set(key, {
        key,
        deviceId: binding.device_id,
        directories: [binding],
      });
  }
  return [...groups.values()];
}

export function environmentSummary(value: EnvironmentSelection) {
  const roots = value.local_roots ?? [];
  const bindings = value.environment_bindings ?? [];
  const directories = roots.length + bindings.length;
  if (!bindings.length)
    return roots.length
      ? `Local · ${directoryCount(roots.length)}`
      : "Local · Chat files";
  const count = deviceEnvironments(bindings).length + (roots.length ? 1 : 0);
  return `${count} environment${count === 1 ? "" : "s"} · ${directoryCount(directories)}`;
}

export function directoryCount(count: number) {
  return `${count} director${count === 1 ? "y" : "ies"}`;
}

export function workingDirectories(
  value: EnvironmentSelection,
  devices: readonly Pick<Schema<"DeviceSummary">, "id" | "name">[] = [],
) {
  const bindings = value.environment_bindings ?? [];
  const deviceLabel = (binding: EnvironmentBinding) =>
    `${devices.find((device) => device.id === binding.device_id)?.name ?? binding.device_id} · ${binding.working_directory}`;
  return [
    ...(value.local_roots ?? []).map((path, index) => ({
      value: localAlias(index),
      label: `Local · ${path || "Empty directory"}`,
    })),
    { value: "thread-files", label: "Local · Chat files" },
    ...bindings.map((binding) => ({
      value: binding.alias,
      label:
        bindings.filter((item) => deviceLabel(item) === deviceLabel(binding))
          .length > 1
          ? `${deviceLabel(binding)} · ${binding.alias}`
          : deviceLabel(binding),
    })),
  ];
}

export function selectedWorkingDirectory(value: EnvironmentSelection) {
  return (
    value.default_environment ??
    (value.local_roots?.length ? "workspace" : "thread-files")
  );
}

/** Existing slots and unrelated policies survive a grouped edit unchanged. */
export function replaceDeviceEnvironment(
  bindings: readonly EnvironmentBinding[],
  previous: readonly EnvironmentBinding[],
  next: readonly EnvironmentBinding[],
  defaultDirectory: string | null,
) {
  const replacements = new Map(
    previous.map((item, index) => [item.alias, next[index]]),
  );
  const selected = previous.findIndex(
    (item) => item.alias === defaultDirectory,
  );
  return {
    environment_bindings: [
      ...bindings.map((item) => replacements.get(item.alias) ?? item),
      ...next.slice(previous.length),
    ],
    default_environment: selected < 0 ? defaultDirectory : next[selected].alias,
  };
}

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
    (_, index) => localAlias(index) === selectedWorkingDirectory(value),
  );
  if (selected < 0) return { local_roots: roots };
  const match = roots.indexOf(previous[selected]);
  const next = match < 0 && roots.length === previous.length ? selected : match;
  if (value.default_environment == null && next === 0)
    return { local_roots: roots };
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
