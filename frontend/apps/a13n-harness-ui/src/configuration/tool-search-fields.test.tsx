// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { parse } from "yaml";
import { ToolSearchFields } from "./tool-search";

vi.mock("../transport/context", () => ({
  useSelectors: () => ({
    data: { mcp_servers: [{ resource_id: "mcp-one", name: "One" }] },
  }),
}));
vi.mock("./root-configuration", () => ({
  useRootConfiguration: () => ({
    configuration: { defaults: { mcp_servers: [] } },
  }),
}));
afterEach(cleanup);

it("enables an empty plan without placeholders and keeps server selection reachable", () => {
  let saved = {};
  function Editor() {
    const [source, setSource] = useState(
      'schema_version: "1"\nkind: agent\nid: agent-one\nname: One\n',
    );
    return (
      <ToolSearchFields
        source={source}
        onChange={(next) => {
          saved = parse(next);
          setSource(next);
        }}
      />
    );
  }
  render(<Editor />);
  fireEvent.click(
    screen.getByRole("switch", { name: "Search tools on demand" }),
  );
  expect(saved).toMatchObject({ tool_search: { enabled: true } });
  expect(
    (saved as { tool_search: { groups?: unknown } }).tool_search.groups,
  ).toBeUndefined();
  fireEvent.click(screen.getByRole("checkbox", { name: "One" }));
  expect(saved).toMatchObject({
    tool_search: { groups: { "mcp-one": { mcp_servers: ["mcp-one"] } } },
  });
  fireEvent.click(screen.getByRole("checkbox", { name: "One" }));
  expect(saved).toMatchObject({ tool_search: { enabled: true, groups: {} } });
  expect(
    screen.getByRole("checkbox", { name: "One" }).getAttribute("aria-checked"),
  ).toBe("false");
});
