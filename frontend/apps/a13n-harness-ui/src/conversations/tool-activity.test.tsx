// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { MemoryRouter, Route, Routes } from "react-router";
import { ToolActivity, ToolCall } from "./tool-call";
import {
  activitySummary,
  describeTool,
  savedToolGroups,
  type ToolView,
} from "./tool-presentation";
import { LiveOutput, SavedEntry } from "./transcript";
import type { Schema } from "../transport/client";

afterEach(cleanup);
const completed = (name: string, input = {}): ToolView => ({
  id: name,
  name,
  input,
  result: { ok: true },
  inputComplete: true,
  stopped: true,
});

it("groups saved exploration across request/response entries without hiding an orphan or crossing prose", () => {
  const entries = [
    {
      position: 0,
      parts: [
        {
          kind: "tool_result",
          tool_name: "other",
          tool_call_id: "edge",
          value: "orphan",
        },
        {
          kind: "tool_call",
          tool_name: "view",
          tool_call_id: "read",
          value: { file_path: "/tmp/one" },
        },
      ],
    },
    {
      position: 1,
      parts: [
        {
          kind: "tool_result",
          tool_call_id: "read",
          value: { content: "one" },
        },
        {
          kind: "tool_call",
          tool_name: "grep",
          tool_call_id: "find",
          value: { pattern: "needle" },
        },
      ],
    },
    {
      position: 2,
      parts: [
        { kind: "tool_result", tool_call_id: "find", value: { count: 1 } },
        { kind: "assistant", text: "Boundary" },
        {
          kind: "tool_call",
          tool_name: "ls",
          tool_call_id: "list",
          value: { path: "/tmp" },
        },
      ],
    },
  ] as Schema<"TranscriptEntry">[];
  const groups = savedToolGroups(entries);
  expect(groups.get(entries[0].parts[1])?.map((tool) => tool.name)).toEqual([
    "view",
    "grep",
  ]);
  expect(groups.get(entries[1].parts[1])).toBeNull();
  render(
    <>
      {entries.map((entry) => (
        <SavedEntry key={entry.position} entry={entry} toolGroups={groups} />
      ))}
    </>,
  );
  expect(screen.getAllByRole("button", { name: /Explored/ })).toHaveLength(2);
  expect(screen.queryByText("Read content")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: /Explored 1 file · 1 lookup/ }),
  );
  expect(screen.getByText("Read content")).toBeTruthy();
  expect(document.querySelectorAll("[data-operation-id]")).toHaveLength(2);
  expect(screen.getByText("Boundary", { selector: "p" })).toBeTruthy();
});

it("keeps shell command/output reads in one disclosure and late failures visible without closing it", () => {
  const shell = completed("shell_exec", { command: "check" });
  const wait = completed("shell_wait", { process_id: "process-1" });
  const view = render(<ToolActivity tools={[shell, wait]} />);
  fireEvent.click(screen.getByRole("button", { name: "Ran 1 command" }));
  expect(screen.getByText("Command")).toBeTruthy();
  view.rerender(
    <ToolActivity
      tools={[
        shell,
        {
          ...wait,
          result: {
            ok: true,
            status: { exit_code: 1 },
            stderr: { text: "Check failed" },
          },
        },
      ]}
    />,
  );
  expect(
    screen.getByRole("button", { name: /Shell/ }).getAttribute("aria-expanded"),
  ).toBe("true");
  expect(screen.getByText("Check failed")).toBeTruthy();
});

it("keeps attention states in collapsed groups without claiming an unrecorded result completed", () => {
  for (const tool of [
    { id: "unknown", name: "edit", stopped: true },
    { ...completed("view"), outcome: "denied" as const },
    { ...completed("web_search"), outcome: "interrupted" as const },
    { ...completed("grep"), retry: true },
  ])
    expect(activitySummary([tool]).issue).toBe(true);
  const pending = { id: "pending", name: "shell_exec", inputComplete: true };
  expect(activitySummary([pending]).status).toBe("1 running");
  expect(
    describeTool({
      ...completed("run_thread"),
      resultOmitted: true,
      result: undefined,
    }).phase,
  ).not.toBe("Run accepted");
});

it("surfaces the OpenAI adapter's failed native-search status in live and saved groups", () => {
  // NativeToolReturnPart keeps outcome=success even when OpenAI reports status=failed.
  const tool: ToolView = {
    id: "native-failed",
    name: "web_search",
    provider: "openai",
    input: { type: "search", query: "docs" },
    result: { status: "failed" },
    outcome: "success",
    inputComplete: true,
    stopped: true,
  };
  expect(describeTool(tool).phase).toBe("Failed");
  expect(activitySummary([tool])).toMatchObject({
    issue: true,
    status: "",
  });
  const entries = [
    {
      position: 0,
      parts: [
        {
          kind: "tool_call",
          tool_name: tool.name,
          tool_call_id: tool.id,
          provider: tool.provider,
          value: tool.input,
        },
        {
          kind: "tool_result",
          tool_name: tool.name,
          tool_call_id: tool.id,
          provider: tool.provider,
          value: tool.result,
          outcome: tool.outcome,
        },
      ],
    },
  ] as Schema<"TranscriptEntry">[];
  const saved = savedToolGroups(entries).get(entries[0].parts[0])!;
  render(<ToolActivity tools={saved} />);
  const toggle = screen.getByRole("button", {
    name: /Browsed the web/,
  });
  expect(toggle.textContent).not.toContain("Failed");
  fireEvent.click(toggle);
  expect(screen.getByText("Failed")).toBeTruthy();
  expect(describeTool({ ...tool, provider: undefined }).failed).toBe(false);
});

it("groups web operations and preserves message and tool category boundaries in live output", () => {
  render(
    <LiveOutput
      gap={false}
      blocks={[
        {
          id: "search",
          kind: "tool",
          name: "web_search",
          text: '{"query":"docs"}',
          result: "{}",
          done: true,
        },
        {
          id: "fetch",
          kind: "tool",
          name: "fetch",
          text: '{"url":"https://example.test"}',
          result: "{}",
          done: true,
        },
        { id: "text", kind: "assistant", text: "Found evidence" },
        {
          id: "shell",
          kind: "tool",
          name: "shell_exec",
          text: '{"command":"check"}',
          result: "{}",
          done: true,
        },
        {
          id: "file",
          kind: "tool",
          name: "edit",
          text: '{"file_path":"/work/a"}',
          result: "{}",
          done: true,
        },
      ]}
    />,
  );
  expect(
    screen.getByRole("button", { name: "Browsed the web · 2 actions" }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Ran 1 command" })).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "File changes · 1 file" }),
  ).toBeTruthy();
  expect(document.querySelectorAll("[data-activity]")).toHaveLength(3);
});

it("navigates to a created Thread inline even when run admission failed, without toggling details", () => {
  render(
    <MemoryRouter>
      <Routes>
        <Route
          path="/"
          element={
            <ToolCall
              tool={{
                id: "create",
                name: "create_thread",
                input: { title: "Worker", prompt: "Investigate" },
                result: {
                  ok: false,
                  thread_id: "thread-new",
                  error: { message: "Model unavailable" },
                },
              }}
            />
          }
        />
        <Route path="/threads/:threadId" element={<p>Worker conversation</p>} />
      </Routes>
    </MemoryRouter>,
  );
  const link = screen.getByRole("link", { name: "Worker" });
  expect(link.closest("button")).toBeNull();
  expect(screen.queryByText("Model unavailable")).toBeNull();
  fireEvent.click(link);
  expect(screen.getByText("Worker conversation")).toBeTruthy();
});

it.each(["run_thread", "steer_thread", "send_thread_message"])(
  "links %s to its exact argument target and distinguishes admission from completion",
  (name) => {
    render(
      <MemoryRouter>
        <ToolCall
          tool={{
            id: "target",
            name,
            input: { thread_id: "thread / target", message: "Question" },
            result: {
              ok: true,
              accepted: true,
              mode: "steer",
              receipt: { receipt_id: "receipt-one" },
            },
          }}
        />
      </MemoryRouter>,
    );
    expect(
      screen
        .getByRole("link", { name: "Open conversation" })
        .getAttribute("href"),
    ).toBe("/threads/thread%20%2F%20target");
    expect(screen.queryByText(/completed/i)).toBeNull();
    expect(
      describeTool({
        id: "reject",
        name: "steer_thread",
        result: { ok: false, accepted: false },
      }).phase,
    ).toBe("Not accepted");
  },
);

it("refines all collaboration tools and exposes discovered Thread links on expansion", () => {
  const names = [
    "list_threads",
    "get_thread",
    "list_projects",
    "get_project",
    "list_agents",
    "list_models",
    "create_thread",
    "run_thread",
    "steer_thread",
    "send_thread_message",
  ];
  for (const name of names)
    expect(describeTool(completed(name)).label).not.toBe(name);
  render(
    <MemoryRouter>
      <ToolCall
        tool={{
          ...completed("list_threads"),
          result: {
            threads: [{ thread_id: "thread-one", title: "Research" }],
            total: 1,
          },
        }}
      />
    </MemoryRouter>,
  );
  fireEvent.click(screen.getByRole("button", { name: /Find conversations/ }));
  expect(
    screen.getByRole("link", { name: "Research" }).getAttribute("href"),
  ).toBe("/threads/thread-one");
  expect(screen.getByText("1 threads found")).toBeTruthy();
});

it("retains saved applied diffs and explicit omission without reconstructing requested input", () => {
  const entries = [
    {
      position: 0,
      parts: [
        {
          kind: "tool_call",
          tool_name: "edit",
          tool_call_id: "edit",
          value: {
            file_path: "/work/a",
            old_string: "requested",
            new_string: "replacement",
          },
        },
        {
          kind: "tool_result",
          tool_name: "edit",
          tool_call_id: "edit",
          value: { ok: true },
          applied_edit: {
            file_path: "/work/a",
            before: "observed before\n",
            after: "observed after\n",
            omitted: false,
          },
        },
        {
          kind: "tool_call",
          tool_name: "edit",
          tool_call_id: "big",
          value: {
            file_path: "/work/b",
            old_string: "requested",
            new_string: "replacement",
          },
        },
        {
          kind: "tool_result",
          tool_name: "edit",
          tool_call_id: "big",
          value: { ok: true },
          applied_edit: {
            file_path: "/work/b",
            before: null,
            after: null,
            omitted: true,
          },
        },
      ],
    },
  ] as Schema<"TranscriptEntry">[];
  render(<SavedEntry entry={entries[0]} />);
  fireEvent.click(
    screen.getByRole("button", { name: "File changes · 2 files" }),
  );
  expect(screen.getByText("-observed before")).toBeTruthy();
  expect(screen.getByText("+observed after")).toBeTruthy();
  expect(screen.getByText(/This older record did not retain/)).toBeTruthy();
  expect(screen.queryByText("Requested replacement")).toBeNull();
});

it("does not pair provider search results with local calls sharing an ID", () => {
  const entries = [
    {
      position: 0,
      parts: [
        {
          kind: "tool_call",
          tool_name: "web_search",
          tool_call_id: "same",
          provider: "openai",
          value: { query: "provider query" },
        },
        {
          kind: "tool_call",
          tool_name: "web_search",
          tool_call_id: "same",
          value: { query: "local query" },
        },
        {
          kind: "tool_result",
          tool_name: "web_search",
          tool_call_id: "same",
          provider: "openai",
          value: { status: "completed" },
        },
      ],
    },
  ] as Schema<"TranscriptEntry">[];
  const tools = savedToolGroups(entries).get(entries[0].parts[0])!;
  expect(tools[0].result).toEqual({ status: "completed" });
  expect(tools[1].result).toBeUndefined();
  expect(activitySummary(tools).issue).toBe(true);
});
