import { expect, it } from "vitest";
import { selectSearchServer } from "./tool-search";
it("changes only the selected server and preserves custom groups and proxy settings", () => {
  const original = {
    enabled: false,
    config: { call_name: "invoke" },
    groups: {
      knowledge: {
        description: "Our documents",
        mcp_servers: ["mcp-one", "mcp-two"],
        harness_plugins: ["plugin-memory"],
      },
    },
  };
  const next = selectSearchServer(original, "mcp-one", "One", false);
  expect(next.enabled).toBe(false);
  expect(next.config).toEqual(original.config);
  expect(next.groups?.knowledge).toEqual({
    ...original.groups.knowledge,
    mcp_servers: ["mcp-two"],
  });
  expect(original.groups.knowledge.mcp_servers).toEqual(["mcp-one", "mcp-two"]);
});
it("uses stable distinct group names when server identifiers have the same prefix", () => {
  const prefix = "mcp-" + "x".repeat(30);
  const first = selectSearchServer({}, prefix + "one", "One", true);
  const second = selectSearchServer(first, prefix + "two", "Two", true);
  expect(Object.keys(second.groups!)).toHaveLength(2);
  expect(Object.keys(second.groups!).every((name) => name.length <= 32)).toBe(
    true,
  );
});

it("does not accumulate empty groups when a server is repeatedly toggled", () => {
  let plan = selectSearchServer({}, "mcp-one", "One", true);
  const keys = Object.keys(plan.groups!);
  for (let attempt = 0; attempt < 3; attempt++) {
    plan = selectSearchServer(plan, "mcp-one", "One", false);
    expect(plan.groups).toEqual({});
    plan = selectSearchServer(plan, "mcp-one", "One", true);
    expect(Object.keys(plan.groups!)).toEqual(keys);
  }
});

it("retains groups with plugin or capability sources when their last MCP server is removed", () => {
  const plan = {
    groups: {
      plugin: {
        description: "Plugin",
        mcp_servers: ["mcp-one"],
        harness_plugins: ["plugin-one"],
      },
      capability: { description: "Capability", capabilities: ["native"] },
    },
  };
  expect(selectSearchServer(plan, "mcp-one", "One", false).groups).toEqual({
    plugin: { ...plan.groups.plugin, mcp_servers: [] },
    capability: plan.groups.capability,
  });
});

it("reuses an existing empty key without losing its custom description", () => {
  const plan = {
    groups: { "mcp-one": { description: "Custom search", mcp_servers: [] } },
  };
  expect(selectSearchServer(plan, "mcp-one", "One", true).groups).toEqual({
    "mcp-one": { description: "Custom search", mcp_servers: ["mcp-one"] },
  });
});
