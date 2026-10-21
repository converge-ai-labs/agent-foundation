import { renderToStaticMarkup } from "react-dom/server";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import type { PresentedItem } from "../projection";
import { AgentTurn } from "./assistant-message";
import { fixtureExecution, fixtureTimeline, retainedTimeline } from "./fixture";
import { transcriptBlocks } from "./items";

afterEach(cleanup);

// The same translator the Debug rows are tested with, so the two levels can be
// compared on the words they actually produce.
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      options
        ? key.replace(/{{(\w+)}}/g, (_, name) => String(options[name] ?? ""))
        : key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

function item(
  id: string,
  kind: string,
  fields: Partial<PresentedItem> = {},
): PresentedItem {
  return {
    id,
    kind,
    state: "completed",
    firstPosition: "1-0",
    lastPosition: "1-0",
    startedAt: null,
    endedAt: null,
    text: "",
    role: "assistant",
    toolName: "",
    arguments: "",
    protectedReasoning: false,
    ...fields,
  };
}

const tool = (id: string, name: string, args: unknown, fields = {}) =>
  item(id, "tool_call", {
    toolName: name,
    arguments: JSON.stringify(args),
    ...fields,
  });

/** Chat renders the same timeline Debug does, read from the same Items. */
const blocks = (items: PresentedItem[], runState: string) =>
  transcriptBlocks(
    retainedTimeline(
      items.map((entry, index) => ({
        ...entry,
        firstPosition: `${index + 1}-0`,
        lastPosition: `${index + 1}-0`,
      })),
    ).entries,
    runState,
  );

it("names the agent once and reads its work in one line before the answer", () => {
  const markup = renderToStaticMarkup(
    <AgentTurn
      agentName="Release reviewer"
      blocks={blocks(
        [
          item("input", "text_message", {
            role: "user",
            text: "Private input",
          }),
          item("thought", "reasoning_message", {
            text: "Checking the fold before editing",
          }),
          tool("tool-1", "read_file", { path: "src/stream.ts" }),
          tool("tool-2", "shell_exec", { command: "npm test" }),
          item("reply", "text_message", { text: "Final response" }),
        ],
        "completed",
      )}
    />,
  );
  expect(markup).not.toContain("Private input");
  const positions = [
    "Release reviewer",
    "read_file",
    "src/stream.ts",
    "shell_exec",
    "npm test",
    "Final response",
  ].map((label) => markup.indexOf(label));
  expect(positions.every((position) => position >= 0)).toBe(true);
  expect(positions).toEqual([...positions].sort((a, b) => a - b));
  expect(markup.match(/<strong>Release reviewer<\/strong>/g)).toHaveLength(1);
  // Reasoning is named and counted, but the line only names the steps.
  expect(markup).toContain("and 1 more");
  expect(markup).toContain('data-state="done"');
});

it("shows reasoning on request and preserves the reader's choice as tools update", () => {
  const turn = (state: string) => (
    <AgentTurn
      blocks={blocks(
        [
          item("thought", "reasoning_message", {
            text: "Checking the fold before editing",
          }),
          tool("open", "shell_exec", { command: "npm test" }, { state }),
        ],
        "running",
      )}
    />
  );
  const { rerender } = render(turn("in_progress"));
  const trigger = screen.getByRole("button", { name: /shell_exec/ });
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("list")).toBeNull();

  rerender(turn("completed"));
  rerender(turn("in_progress"));
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("list")).toBeNull();

  fireEvent.click(trigger);
  expect(screen.getByText("Reasoning")).toBeTruthy();
  expect(screen.getByText("Checking the fold before editing")).toBeTruthy();
  rerender(turn("completed"));
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
  expect(screen.getByRole("list")).toBeTruthy();

  fireEvent.click(trigger);
  rerender(turn("in_progress"));
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  expect(screen.queryByRole("list")).toBeNull();
});

it("says what it is waiting for instead of opening the rows", () => {
  const markup = renderToStaticMarkup(
    <AgentTurn
      blocks={blocks(
        [
          tool(
            "open",
            "shell_exec",
            { command: "echo approved > marker.txt" },
            { state: "interrupted" },
          ),
        ],
        "waiting",
      )}
    />,
  );
  // Retained messages alone do not say what the call waits for, so the line
  // claims no more than that it is the reader's turn.
  expect(markup).toContain("Waiting for your response");
  expect(markup).toContain('aria-expanded="false"');
});

it("keeps running work collapsed while showing its status", () => {
  const markup = renderToStaticMarkup(
    <AgentTurn
      blocks={blocks(
        [
          tool(
            "open",
            "shell_exec",
            { command: "npm test" },
            { state: "in_progress" },
          ),
        ],
        "running",
      )}
    />,
  );
  expect(markup).toContain('aria-expanded="false"');
  expect(markup).toContain('data-state="working"');
});

it("does not keep interrupted work spinning, and shows guidance sent mid-run", () => {
  const markup = renderToStaticMarkup(
    <AgentTurn
      blocks={blocks(
        [
          tool(
            "open",
            "shell_exec",
            { command: "npm test" },
            { state: "in_progress" },
          ),
          item("steer", "text_message", {
            role: "user",
            text: "Use the smaller scope",
            steeringSource: "harness",
          }),
        ],
        "cancelled",
      )}
    />,
  );
  expect(markup).not.toContain('aria-label="Working"');
  expect(markup).toContain('data-state="interrupted"');
  expect(markup).toContain("Guidance");
  expect(markup).toContain("Use the smaller scope");
});

it("keeps fallback output and error details inside the same agent response", () => {
  const markup = renderToStaticMarkup(
    <AgentTurn blocks={[]} agentName="Reviewer">
      <p>Fallback output</p>
      <p>Failure detail</p>
    </AgentTurn>,
  );
  expect(markup).toMatch(
    /Agent response.*Reviewer.*Fallback output.*Failure detail.*<\/section>/,
  );
});

it("carries the same edits and delegations Debug reports", () => {
  const execution = fixtureExecution();
  execution.steps = [
    ...execution.steps,
    {
      id: "step_delegate",
      scope: "1",
      kind: "subagent",
      name: "Researcher",
      state: "completed",
      items: [],
      position: "2-5",
      startedAt: "2026-09-20T10:00:03.000Z",
      endedAt: "2026-09-20T10:00:06.000Z",
      parentId: "step_model",
      dispatchOnly: true,
    },
    {
      id: "step_running",
      scope: "1",
      kind: "tool",
      name: "shell_exec",
      state: "running",
      items: [],
      position: "2-6",
      startedAt: "2026-09-20T10:00:06.000Z",
      endedAt: null,
      parentId: "step_model",
    },
  ];
  const { container } = render(
    <MemoryRouter>
      <AgentTurn
        blocks={transcriptBlocks(
          fixtureTimeline({ execution }).entries,
          "running",
        )}
        childPath="/workspace/design/sessions/ses_1/threads/thr_child/runs/run_child?view=debug"
      />
    </MemoryRouter>,
  );
  for (const trigger of screen.getAllByRole("button", { expanded: false })) {
    fireEvent.click(trigger);
  }
  const markup = container.innerHTML;
  expect(markup).toContain("Delegated to Researcher");
  // The edit badges are the ones the Debug timeline computed.
  expect(markup).toContain("+2");
  expect(markup).toContain("−1");
  expect(markup).toContain("Open child thread");
  expect(markup).toContain(
    'href="/workspace/design/sessions/ses_1/threads/thr_child/runs/run_child?view=debug"',
  );
});
