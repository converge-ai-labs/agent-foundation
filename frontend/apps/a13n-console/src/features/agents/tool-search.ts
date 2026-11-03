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

export type SearchSource = {
  kind: "capabilities" | "connections" | "plugins" | "platform";
  id: string;
  label: string;
  description: string;
  groupName: string;
  defaultDiscovery: boolean;
  available: boolean;
};

export function searchSources(
  config: Pick<AgentConfig, "toolsets" | "connection_tools" | "plugins">,
  catalog: Schema["ToolsetCatalog"],
): SearchSource[] {
  const enabled = (group: Schema["ToolsetDefinition"]) =>
    (config.toolsets?.[group.key]?.enabled ?? group.default_enabled) &&
    group.tools.some(
      (tool) =>
        config.toolsets?.[group.key]?.tools?.[tool.key]?.enabled ??
        tool.default_enabled,
    );
  return [
    ...catalog.discovery_sources
      .filter(
        (source) =>
          source.group_name !== null &&
          (source.kind !== "connections" ||
            config.connection_tools?.some(
              (item) => item.connection_id === source.id,
            )),
      )
      .map((source) => ({
        kind: source.kind,
        id: source.id,
        label: source.display_name,
        description: source.description,
        groupName: source.group_name!,
        defaultDiscovery:
          source.kind === "capabilities"
            ? catalog.discovery_defaults.capabilities?.includes(
                source.id as Capability,
              ) === true
            : source.kind === "platform"
              ? catalog.discovery_defaults.exclude_platform === false
              : catalog.discovery_defaults.connections === true,
        available:
          source.kind === "capabilities"
            ? catalog.items.some(
                (group) => group.key === source.id && enabled(group),
              )
            : source.kind === "platform"
              ? catalog.items.some((group) => group.platform && enabled(group))
              : config.connection_tools?.find(
                  (item) => item.connection_id === source.id,
                )?.tools?.length !== 0,
      })),
    ...(config.plugins ?? []).map((plugin) => ({
      kind: "plugins" as const,
      id: plugin.instance_name,
      label: plugin.instance_name,
      description: `Tools from plugin ${plugin.instance_name}.`,
      groupName: "plugin",
      defaultDiscovery: false,
      available: true,
    })),
  ];
}

/** Automatic discovery follows connected MCP sources as a category. */
export function automaticSources(
  sources: SearchSource[],
  catalog: Schema["ToolsetCatalog"],
): SearchSource[] {
  const connections = catalog.discovery_sources.find(
    (source) => source.group_name === null,
  )!;
  return [
    ...sources.filter(
      (source) => source.kind === "capabilities" || source.kind === "platform",
    ),
    {
      kind: "connections",
      id: "all",
      label: connections.display_name,
      description: connections.description,
      groupName: "",
      defaultDiscovery: catalog.discovery_defaults.connections === true,
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
  source: Pick<SearchSource, "kind" | "id" | "defaultDiscovery">,
): boolean {
  if (search.mode === "direct") return false;
  if (search.automatic == null)
    return search.groups.some((group) => groupHasSource(group, source));
  if (source.kind === "plugins") return false;
  if (source.kind === "platform")
    return search.automatic.exclude_platform == null
      ? source.defaultDiscovery
      : !search.automatic.exclude_platform;
  if (source.kind === "connections")
    return (
      (search.automatic.connections ?? source.defaultDiscovery) &&
      (source.id === "all" ||
        !search.automatic.exclude_connections?.includes(source.id))
    );
  return (
    search.automatic.capabilities?.includes(source.id as Capability) ??
    source.defaultDiscovery
  );
}

function sourceGroup(source: SearchSource): SearchGroup {
  return {
    name: source.groupName,
    description: source.description,
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
    .map((source) => sourceGroup(source));
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
    if (source.kind === "connections") {
      if (source.id === "all")
        return { ...search, automatic: { ...policy, connections: checked } };
      const excluded = policy.exclude_connections ?? [];
      return {
        ...search,
        automatic: {
          ...policy,
          exclude_connections: checked
            ? excluded.filter((id) => id !== source.id)
            : [...new Set([...excluded, source.id])],
        },
      };
    }
    if (source.kind === "plugins") return search;
    const selected =
      policy.capabilities ??
      sources
        .filter((item) => item.kind === "capabilities" && item.defaultDiscovery)
        .map((item) => item.id as Capability);
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
  const group = sourceGroup(source);
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
