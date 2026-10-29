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
