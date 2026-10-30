import type { AgentConfig } from "./configuration";

type ToolSearch = NonNullable<AgentConfig["tool_search"]>;
export type SearchGroup = NonNullable<ToolSearch["groups"]>[string] & {
  name: string;
};
export interface ToolSearchDraft {
  mode: "direct" | "proxy";
  groups: SearchGroup[];
  config?: ToolSearch["config"];
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
  };
}

export function toolSearchConfig(
  draft: ToolSearchDraft,
  connections: NonNullable<AgentConfig["connection_tools"]>,
): AgentConfig["tool_search"] {
  if (draft.mode === "direct") return undefined;
  const selected = new Set(connections.map((item) => item.connection_id));
  const names = new Set<string>();
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
