import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it } from "vitest";
import { MapExecution } from "./session-map-execution";
import type { Execution } from "../execution";

afterEach(cleanup);
it("previews flat action content on hover without expansion controls or duplicate tool rows", async () => {
  const execution: Execution = {
    steps: [
      {
        id: "model",
        scope: "root",
        kind: "llm",
        state: "completed",
        items: [],
      },
      {
        id: "tool",
        scope: "root",
        kind: "tool",
        name: "search_docs",
        state: "completed",
        items: ["item"],
      },
    ],
    items: new Map([
      [
        "item",
        {
          id: "item",
          kind: "tool_call",
          state: "completed",
          parentId: null,
          firstCursor: "1-0",
          lastCursor: "2-0",
          text: "",
          role: "assistant",
          toolName: "search_docs",
          arguments: '{"query":"Release"}',
          result: "Found the release guide",
          protectedReasoning: false,
        },
      ],
    ]),
  };
  const { container } = render(<MapExecution execution={execution} />);
  expect(container.querySelector("summary")).toBeNull();
  expect(screen.queryByText(/Step \d/)).toBeNull();
  expect(screen.getAllByText("search_docs")).toHaveLength(1);
  await userEvent.setup().hover(screen.getByText("Tool call"));
  expect(await screen.findByRole("tooltip")).toBeTruthy();
  expect(screen.getByText("Found the release guide")).toBeTruthy();
  expect(screen.getByText("Arguments")).toBeTruthy();
});
it("keeps actual child actions nested while keyboard focus opens their preview", async () => {
  const execution: Execution = {
    steps: [
      {
        id: "parent",
        scope: "root",
        kind: "subagent",
        name: "Researcher",
        childScope: "child",
        state: "running",
        items: [],
      },
      { id: "child", scope: "child", kind: "llm", state: "running", items: [] },
    ],
    items: new Map(),
  };
  render(<MapExecution execution={execution} />);
  expect(
    screen
      .getByText("Subagent")
      .closest("li")
      ?.contains(screen.getByText("LLM call")),
  ).toBe(true);
  const user = userEvent.setup();
  await user.tab();
  expect(await screen.findByRole("tooltip")).toBeTruthy();
  expect(document.activeElement?.textContent).toContain("Researcher");
});

it("labels an asynchronous delegation as dispatched instead of child completion", async () => {
  const execution: Execution = {
    steps: [
      {
        id: "dispatch",
        scope: "root",
        kind: "subagent",
        name: "reviewer",
        state: "completed",
        dispatchOnly: true,
        items: [],
      },
    ],
    items: new Map(),
  };
  render(<MapExecution execution={execution} />);
  await userEvent.setup().hover(screen.getByText("Subagent"));
  const tooltip = await screen.findByRole("tooltip");
  expect(tooltip.textContent).toContain("Dispatched");
  expect(tooltip.textContent).toContain(
    "Child progress is tracked in its own thread.",
  );
  expect(tooltip.textContent).not.toContain("Completed");
});
