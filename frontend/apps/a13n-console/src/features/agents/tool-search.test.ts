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
  expect(saved?.automatic).toEqual({ exclude_connections: ["con_excluded"] });
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
    expect(
      toolSearchConfig(draft, [{ connection_id: "con_saved" }])?.groups,
    ).toEqual(plan?.groups);
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
    "connection-1",
    "connection-2",
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
  expect(toolSearchConfig(draft, [])?.automatic?.capabilities).toEqual([
    "computer",
    "browser",
    "files",
  ]);
  const mcp = automaticSources(sources, catalog.discovery_defaults).find(
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
