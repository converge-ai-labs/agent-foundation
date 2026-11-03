import type { AgentConfig } from "./configuration";
import type { Schema } from "../../shared/api";

type ToolSearch = NonNullable<AgentConfig["tool_search"]>;
export type SearchGroup = NonNullable<ToolSearch["groups"]>[string] & {
  name: string;
};
export interface ToolSearchDraft {
  mode: "direct" | "proxy";
  groups: SearchGroup[];
  config?: ToolSearch["config"];
  automatic?: ToolSearch["automatic"];
}

type Capability = NonNullable<
  NonNullable<ToolSearch["automatic"]>["capabilities"]
>[number];
const defaultCapabilities: Capability[] = ["computer", "browser"];

export type SearchSource = {
  kind: "capabilities" | "connections" | "plugins" | "platform";
  id: string;
  label: string;
  available: boolean;
};

export function searchSources(
  config: Pick<AgentConfig, "toolsets" | "connection_tools" | "plugins">,
  catalog: readonly Schema["ToolsetDefinition"][],
): SearchSource[] {
  const enabled = (group: Schema["ToolsetDefinition"]) =>
    (config.toolsets?.[group.key]?.enabled ?? group.default_enabled) &&
    group.tools.some(
      (tool) =>
        config.toolsets?.[group.key]?.tools?.[tool.key]?.enabled ??
        tool.default_enabled,
    );
  return [
    ...Object.entries({
      computer: "Computer",
      browser: "Browser",
      files: "Files",
      shell: "Terminal",
      web: "Web",
      memory: "Memory",
      assets: "Asset",
    }).map(([id, label]) => ({
      kind: "capabilities" as const,
      id,
      label,
      available: catalog.some((group) => group.key === id && enabled(group)),
    })),
    {
      kind: "platform",
      id: "platform",
      label: "Platform tools",
      available: catalog.some((group) => group.platform && enabled(group)),
    },
    ...(config.connection_tools ?? []).map((connection) => ({
      kind: "connections" as const,
      id: connection.connection_id,
      label: connection.connection_id,
      available: connection.tools?.length !== 0,
    })),
    ...(config.plugins ?? []).map((plugin) => ({
      kind: "plugins" as const,
      id: plugin.instance_name,
      label: plugin.instance_name,
      available: true,
    })),
  ];
}

/** Automatic discovery follows connected MCP sources as a category. */
export function automaticSources(sources: SearchSource[]): SearchSource[] {
  return [
    ...sources.filter(
      (source) => source.kind === "capabilities" || source.kind === "platform",
    ),
    {
      kind: "connections",
      id: "all",
      label: "MCP tools",
      available: sources.some(
        (source) => source.kind === "connections" && source.available,
      ),
    },
  ];
}

export function groupHasSource(
  group: SearchGroup,
  source: Pick<SearchSource, "kind" | "id">,
): boolean {
  return source.kind === "platform"
    ? group.platform === true
    : (group[source.kind] as readonly string[] | undefined)?.includes(
        source.id,
      ) === true;
}

export function usesToolDiscovery(
  search: ToolSearchDraft,
  source: Pick<SearchSource, "kind" | "id">,
): boolean {
  if (search.mode === "direct") return false;
  if (search.automatic == null)
    return search.groups.some((group) => groupHasSource(group, source));
  if (source.kind === "plugins") return false;
  if (source.kind === "platform") return !search.automatic.exclude_platform;
  if (source.kind === "connections")
    return (
      search.automatic.connections !== false &&
      (source.id === "all" ||
        !search.automatic.exclude_connections?.includes(source.id))
    );
  return (search.automatic.capabilities ?? defaultCapabilities).includes(
    source.id as Capability,
  );
}

function sourceGroup(
  source: SearchSource,
  sources: SearchSource[],
): SearchGroup {
  const connectionIndex = sources
    .filter((item) => item.kind === "connections")
    .findIndex((item) => item.id === source.id);
  return {
    name:
      source.kind === "connections"
        ? `connection-${connectionIndex + 1}`
        : source.kind === "plugins"
          ? "plugin"
          : source.id,
    description:
      source.kind === "connections"
        ? `Tools from connection ${source.id}.`
        : source.kind === "platform"
          ? "Manage selected platform resources."
          : `Use ${source.id} tools.`,
    ...(source.kind === "platform"
      ? { platform: true }
      : source.kind === "capabilities"
        ? { capabilities: [source.id as Capability] }
        : { [source.kind]: [source.id] }),
  };
}

export function automaticSearchGroups(
  search: ToolSearchDraft,
  sources: SearchSource[],
): SearchGroup[] {
  return sources
    .filter((source) => usesToolDiscovery(search, source))
    .map((source) => sourceGroup(source, sources));
}

export function automaticPolicy(
  search: ToolSearchDraft,
  sources: SearchSource[],
): NonNullable<ToolSearch["automatic"]> {
  return {
    capabilities: sources
      .filter(
        (source) =>
          source.kind === "capabilities" && usesToolDiscovery(search, source),
      )
      .map((source) => source.id as Capability),
    connections: sources.some(
      (source) =>
        source.kind === "connections" && usesToolDiscovery(search, source),
    ),
    exclude_connections: sources
      .filter(
        (source) =>
          source.kind === "connections" && !usesToolDiscovery(search, source),
      )
      .map((source) => source.id),
    exclude_platform: !sources.some(
      (source) =>
        source.kind === "platform" && usesToolDiscovery(search, source),
    ),
  };
}

export function toggleDiscoverySource(
  search: ToolSearchDraft,
  source: SearchSource,
  checked: boolean,
  sources: SearchSource[],
): ToolSearchDraft {
  if (search.automatic != null) {
    const policy = search.automatic;
    if (source.kind === "platform")
      return {
        ...search,
        automatic: { ...policy, exclude_platform: !checked },
      };
    if (source.kind === "connections")
      return { ...search, automatic: { ...policy, connections: checked } };
    if (source.kind === "plugins") return search;
    const selected = policy.capabilities ?? defaultCapabilities;
    return {
      ...search,
      automatic: {
        ...policy,
        capabilities: checked
          ? [...selected, source.id as Capability]
          : selected.filter((id) => id !== source.id),
      },
    };
  }
  if (!checked)
    return {
      ...search,
      groups: search.groups.map((group) => ({
        ...group,
        ...(source.kind === "platform"
          ? { platform: false }
          : {
              [source.kind]: (group[source.kind] ?? []).filter(
                (id) => id !== source.id,
              ),
            }),
      })),
    };
  const group = sourceGroup(source, sources);
  const base = group.name;
  for (
    let index = 2;
    search.groups.some((item) => item.name === group.name);
    index++
  )
    group.name = `${base.slice(0, 28)}-${index}`;
  return { ...search, groups: [...search.groups, group] };
}

export function toolSearchDraft(
  value: AgentConfig["tool_search"],
): ToolSearchDraft {
  return {
    mode: value == null ? "direct" : "proxy",
    groups: Object.entries(value?.groups ?? {}).map(([name, group]) => ({
      name,
      ...group,
      ...(group.capabilities?.includes("environment")
        ? {
            capabilities: group.capabilities.flatMap((key) =>
              key === "environment"
                ? (["files", "shell", "computer"] as const)
                : [key],
            ),
          }
        : {}),
    })),
    config: value?.config,
    automatic: value == null ? {} : (value.automatic ?? null),
  };
}

export function toolSearchConfig(
  draft: ToolSearchDraft,
  connections: NonNullable<AgentConfig["connection_tools"]>,
): AgentConfig["tool_search"] {
  if (draft.mode === "direct") return undefined;
  const selected = new Set(connections.map((item) => item.connection_id));
  if (draft.automatic != null)
    return {
      config: { ...draft.config, mode: "proxy" },
      automatic: {
        ...draft.automatic,
        ...(draft.automatic.exclude_connections
          ? {
              exclude_connections: draft.automatic.exclude_connections.filter(
                (id) => selected.has(id),
              ),
            }
          : {}),
      },
    };
  const names = new Set<string>();
  if (draft.groups.length > 128)
    throw new Error("Use at most 128 custom tool groups.");
  for (const group of draft.groups) {
    if (
      !/^[A-Za-z][A-Za-z0-9_-]{0,31}$/.test(group.name) ||
      group.name.includes("__")
    )
      throw new Error(
        "Group names use 1–32 letters, digits, underscores or hyphens, start with a letter, and omit double underscores.",
      );
    if (names.has(group.name))
      throw new Error("Tool search group names must be unique.");
    if (!group.description.trim())
      throw new Error("Each tool search group needs a description.");
    names.add(group.name);
  }
  return {
    config: { ...draft.config, mode: "proxy" },
    groups: Object.fromEntries(
      draft.groups.map(({ name, ...group }) => [
        name,
        {
          ...group,
          // Removing a selected connection also removes its presentation membership.
          connections: group.connections?.filter((source) =>
            selected.has(source),
          ),
        },
      ]),
    ),
  };
}
