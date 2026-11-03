import { expect, it } from "vitest";
import {
  automaticSearchGroups,
  toolSearchConfig,
  toolSearchDraft,
  usesToolDiscovery,
  toggleDiscoverySource,
} from "./tool-search";

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
  expect(
    usesToolDiscovery(reopened, { kind: "connections", id: "con_new" }),
  ).toBe(true);
  expect(
    usesToolDiscovery(reopened, { kind: "connections", id: "con_excluded" }),
  ).toBe(false);
  expect(usesToolDiscovery(reopened, { kind: "plugins", id: "custom" })).toBe(
    false,
  );
  expect(toolSearchConfig({ ...reopened, mode: "direct" }, [])).toBeUndefined();
});

it("keeps legacy and explicitly empty group plans custom", () => {
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
    {
      kind: "capabilities",
      id: "computer",
      label: "Computer",
      available: true,
    },
    { kind: "capabilities", id: "web", label: "Web", available: true },
    { kind: "connections", id: "con_one", label: "Same name", available: true },
    { kind: "connections", id: "con_two", label: "Same name", available: true },
    { kind: "platform", id: "platform", label: "Platform", available: false },
  ]);
  expect(groups.map((group) => group.name)).toEqual([
    "computer",
    "connection-1",
    "connection-2",
    "platform",
  ]);
  expect(groups.every((group) => group.description.trim())).toBe(true);
});

it("keeps common tools direct until added, and migrates legacy environment membership without merging sources", () => {
  let draft = toolSearchDraft({ automatic: {} });
  for (const id of ["files", "shell", "web", "memory", "assets"])
    expect(usesToolDiscovery(draft, { kind: "capabilities", id })).toBe(false);
  for (const id of ["computer", "browser"])
    expect(usesToolDiscovery(draft, { kind: "capabilities", id })).toBe(true);
  const files = {
    kind: "capabilities" as const,
    id: "files",
    label: "Files",
    available: true,
  };
  draft = toggleDiscoverySource(draft, files, true, [files]);
  expect(toolSearchConfig(draft, [])?.automatic?.capabilities).toEqual([
    "computer",
    "browser",
    "files",
  ]);
  const mcp = {
    kind: "connections" as const,
    id: "all",
    label: "MCP",
    available: false,
  };
  draft = toggleDiscoverySource(draft, mcp, false, [files]);
  expect(
    usesToolDiscovery(draft, { kind: "connections", id: "con_later" }),
  ).toBe(false);
  const legacy = toolSearchDraft({
    groups: {
      tools: { description: "Environment", capabilities: ["environment"] },
    },
  });
  expect(legacy.groups[0].capabilities).toEqual(["files", "shell", "computer"]);
});
