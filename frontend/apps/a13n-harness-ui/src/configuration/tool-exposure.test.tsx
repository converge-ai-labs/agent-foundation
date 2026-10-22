// @vitest-environment jsdom
import { useState } from "react";
import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { parse } from "yaml";
import { ToolExposureFields, ProgressiveSearchFields } from "./tool-exposure";

afterEach(cleanup);

it.each([{ path: [] }, { path: ["capabilities", "0"] }])(
  "edits source policy at $path without losing source configuration",
  async ({ path }) => {
    function Editor() {
      const [source, setSource] = useState(
        path.length
          ? "capabilities:\n  - capability: web\n    configuration: {provider: local}\n"
          : "kind: mcp_server\ntransport: {url: 'https://mcp.test'}\n",
      );
      return (
        <>
          <ToolExposureFields
            source={source}
            onChange={setSource}
            path={path}
          />
          <output>{source}</output>
        </>
      );
    }
    render(<Editor />);
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("combobox", { name: "Default tool exposure" }),
    );
    await user.click(
      await screen.findByRole("option", {
        name: "Progressive — discover when needed",
      }),
    );
    await user.type(
      screen.getByLabelText("Toolset purpose"),
      "Search knowledge",
    );
    await user.click(screen.getByText("Per-tool overrides"));
    await user.type(screen.getByLabelText("Exact tool name"), "lookup");
    await user.click(screen.getByRole("button", { name: "Add override" }));
    const raw = parse(screen.getByRole("status").textContent ?? "");
    const value = path.length ? raw.capabilities[0] : raw;
    expect(value).toMatchObject({
      exposure: "progressive",
      description: "Search knowledge",
      tool_exposure: { lookup: "progressive" },
    });
    expect(path.length ? value.configuration : value.transport).toEqual(
      path.length ? { provider: "local" } : { url: "https://mcp.test" },
    );
  },
);

it("clears native strategy when switching to Harness discovery", async () => {
  function Editor() {
    const [source, setSource] = useState(
      "progressive_search: native\nprogressive_search_strategy: regex\n",
    );
    return (
      <>
        <ProgressiveSearchFields source={source} onChange={setSource} />
        <output>{source}</output>
      </>
    );
  }
  render(<Editor />);
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("combobox", { name: "Progressive search" }),
  );
  await user.click(
    await screen.findByRole("option", { name: "Harness discovery" }),
  );
  expect(parse(screen.getByRole("status").textContent ?? "")).toEqual({
    progressive_search: "harness",
  });
});
