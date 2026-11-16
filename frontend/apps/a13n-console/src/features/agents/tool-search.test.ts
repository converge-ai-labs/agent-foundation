import { expect, it } from "vitest";
import {
  automaticSearchGroups,
  toolSearchConfig,
  toolSearchDraft,
  usesToolDiscovery,
  toggleDiscoverySource,
  searchSources,
  automaticSources,
  type SearchSource,
} from "./tool-search";
import type { Schema } from "../../shared/api";

const catalog: Schema["ToolsetCatalog"] = {
  items: Object.entries({
    computer: "Computer",
    browser: "Browser",
    files: "Files",
    shell: "Terminal",
    web: "Web",
    memory: "Memory",
    assets: "Asset",
  }).map(([key, display_name]) => ({
    key: key as Schema["ToolsetDefinition"]["key"],
    display_name,
    config_schema: {},
    description: `${display_name} operations.`,
    default_enabled: true,
    tools: [
      {
        key: "use",
        display_name: "Use",
        execution_id: `${key}.use`,
        model_name: `${key}_use`,
        default_enabled: true,
        default_permission: "allow",
        config_schema: {},
        supported_permissions: ["inherit"],
        resource_selector: null,
        deployment_supported: true,
      },
    ],
  })),
  discovery_sources: [
    ...Object.entries({
      computer: "Computer",
      browser: "Browser",
      files: "Files",
      shell: "Terminal",
      web: "Web",
      memory: "Memory",
      assets: "Asset",
    }).map(([id, display_name]) => ({
      kind: "capabilities" as const,
      id,
      display_name,
      description: `${display_name} operations.`,
      group_name: id,
    })),
    {
      kind: "platform",
      id: "platform",
      display_name: "Platform tools",
      description: "Platform operations.",
      group_name: "platform",
    },
    {
      kind: "connections",
      id: "all",
      display_name: "MCP tools",
      description: "Connection tools.",
      group_name: null,
    },
  ],
  discovery_defaults: {
    capabilities: ["computer", "browser"],
    connections: true,
    exclude_connections: [],
    exclude_platform: false,
  },
};
const sources = searchSources({}, catalog);
const findSource = (id: string) => sources.find((source) => source.id === id)!;
const connection = (id: string): SearchSource => ({
  kind: "connections",
  id,
  label: id,
  groupName: `server-${id}`,
  description: "Connection tools.",
  defaultDiscovery: true,
  available: true,
});

it("round-trips automatic mode and follows source changes without opting excluded sources back in", () => {
  const draft = toolSearchDraft({
    automatic: { exclude_connections: ["con_old", "con_excluded"] },
    config: { max_results: 3 },
  });
  const saved = toolSearchConfig(draft, [
    { connection_id: "con_excluded" },
    { connection_id: "con_new" },
  ]);
  if (!saved || "mode" in saved) throw new Error("Expected proxy discovery");
  expect(saved.automatic).toEqual({ exclude_connections: ["con_excluded"] });
  expect(saved?.config?.max_results).toBe(3);
  const reopened = toolSearchDraft(saved);
  expect(usesToolDiscovery(reopened, connection("con_new"))).toBe(true);
  expect(usesToolDiscovery(reopened, connection("con_excluded"))).toBe(false);
  expect(
    usesToolDiscovery(reopened, {
      kind: "plugins",
      id: "custom",
      defaultDiscovery: false,
    }),
  ).toBe(false);
  expect(toolSearchConfig({ ...reopened, mode: "direct" }, [])).toBeUndefined();
});

it("keeps explicitly empty group plans custom", () => {
  const plans: Parameters<typeof toolSearchDraft>[0][] = [
    { groups: {} },
    {
      groups: {
        knowledge: { description: "Lookup", connections: ["con_saved"] },
      },
    },
  ];
  for (const plan of plans) {
    const draft = toolSearchDraft(plan);
    expect(draft.automatic).toBeNull();
    const saved = toolSearchConfig(draft, [{ connection_id: "con_saved" }]);
    if (!saved || "mode" in saved || (plan && "mode" in plan))
      throw new Error("Expected proxy discovery");
    expect(saved.groups).toEqual(plan?.groups);
  }
  expect(toolSearchDraft(undefined).automatic).toEqual({});
});

it("seeds custom groups including dormant selected sources, without display-name collisions", () => {
  const draft = {
    ...toolSearchDraft({ automatic: {} }),
    mode: "proxy" as const,
  };
  const groups = automaticSearchGroups(draft, [
    findSource("computer"),
    findSource("web"),
    connection("con_one"),
    connection("con_two"),
    findSource("platform"),
  ]);
  expect(groups.map((group) => group.name)).toEqual([
    "computer",
    "server-con_one",
    "server-con_two",
    "platform",
  ]);
  expect(groups.every((group) => group.description.trim())).toBe(true);
});

it("keeps common tools direct until added and follows the catalog defaults", () => {
  let draft = toolSearchDraft({ automatic: {} });
  for (const id of ["files", "shell", "web", "memory", "assets"])
    expect(usesToolDiscovery(draft, findSource(id))).toBe(false);
  for (const id of ["computer", "browser"])
    expect(usesToolDiscovery(draft, findSource(id))).toBe(true);
  const files = findSource("files");
  draft = toggleDiscoverySource(draft, files, true, sources);
  expect(toolSearchConfig(draft, [])).toMatchObject({
    automatic: { capabilities: ["computer", "browser", "files"] },
  });
  const mcp = automaticSources(sources, catalog).find(
    (source) => source.id === "all",
  )!;
  draft = toggleDiscoverySource(draft, mcp, false, [files]);
  expect(usesToolDiscovery(draft, connection("con_later"))).toBe(false);
  const changedCatalog = {
    ...catalog,
    discovery_defaults: {
      ...catalog.discovery_defaults,
      capabilities: ["files" as const],
    },
  };
  const changedSources = searchSources({}, changedCatalog);
  const defaults = toolSearchDraft({ automatic: {} });
  expect(
    changedSources
      .filter(
        (source) =>
          source.kind === "capabilities" && usesToolDiscovery(defaults, source),
      )
      .map((source) => source.id),
  ).toEqual(["files"]);
});

it("round-trips native sources without groups and keeps proxy settings separate", () => {
  let draft = toolSearchDraft({
    mode: "native",
    sources: { exclude_connections: ["con_excluded", "con_removed"] },
  });
  expect(draft.mode).toBe("native");
  expect(usesToolDiscovery(draft, connection("con_new"))).toBe(true);
  expect(usesToolDiscovery(draft, connection("con_excluded"))).toBe(false);
  draft = toggleDiscoverySource(draft, findSource("files"), true, sources);
  const saved = toolSearchConfig(draft, [{ connection_id: "con_excluded" }]);
  expect(saved).toEqual({
    mode: "native",
    sources: {
      capabilities: ["computer", "browser", "files"],
      exclude_connections: ["con_excluded"],
    },
  });
  expect(
    toolSearchConfig(toolSearchDraft(saved), [
      { connection_id: "con_excluded" },
    ]),
  ).toEqual(saved);
  const proxy = {
    ...draft,
    mode: "proxy" as const,
    automatic: {},
    config: { max_results: 3 },
    groups: [],
  };
  expect(toolSearchConfig(proxy, [])).toEqual({
    config: { mode: "proxy", max_results: 3 },
    automatic: {},
  });
  expect(
    toolSearchConfig({ ...proxy, mode: "native" }, [
      { connection_id: "con_excluded" },
    ]),
  ).toEqual(saved);
});
