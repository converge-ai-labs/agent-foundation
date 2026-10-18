import type { AgentConfig } from "./configuration";

type ToolProxy = NonNullable<AgentConfig["tool_proxy"]>;
export type SearchGroup = NonNullable<ToolProxy["groups"]>[string] & {
  name: string;
};
export interface ToolSearchDraft {
  mode: "direct" | "proxy" | "native";
  groups: SearchGroup[];
  config?: ToolProxy["config"];
}

export function toolSearchDraft(
  value: AgentConfig["tool_proxy"],
): ToolSearchDraft {
  return {
    mode: value == null ? "direct" : value.native_search ? "native" : "proxy",
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
): AgentConfig["tool_proxy"] {
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
    native_search: draft.mode === "native",
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
    ...(draft.config ? { config: draft.config } : {}),
  };
}
