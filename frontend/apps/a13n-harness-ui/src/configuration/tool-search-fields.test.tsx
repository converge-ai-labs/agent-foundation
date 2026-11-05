// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { useState } from "react";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { parse } from "yaml";
import { ToolSearchFields } from "./tool-search";
import { i18n } from "../i18n";

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
afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("en");
});

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

it("switches language without changing a selected server or its YAML draft", async () => {
  function Editor() {
    const [source, setSource] = useState("kind: agent\nid: a\nname: A\n");
    return (
      <>
        <ToolSearchFields source={source} onChange={setSource} />
        <output data-testid="draft">{source}</output>
      </>
    );
  }
  render(<Editor />);
  fireEvent.click(
    screen.getByRole("switch", { name: "Search tools on demand" }),
  );
  fireEvent.click(screen.getByRole("checkbox", { name: "One" }));
  const draft = screen.getByTestId("draft").textContent;
  await act(async () => {
    await i18n.changeLanguage("zh-CN");
  });
  expect(screen.getByRole("switch", { name: "按需搜索工具" })).toBeTruthy();
  expect(
    screen.getByRole("checkbox", { name: "One" }).getAttribute("aria-checked"),
  ).toBe("true");
  expect(screen.getByTestId("draft").textContent).toBe(draft);
});
