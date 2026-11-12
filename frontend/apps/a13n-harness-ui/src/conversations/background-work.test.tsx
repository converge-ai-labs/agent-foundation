// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import {
  backgroundGroups,
  backgroundItems,
  glyphIdentity,
} from "./background-work";
import { BackgroundScope } from "./background-context";
import { BackgroundCard, BackgroundToggle } from "./background-card";
import { FocusDisplay } from "./stream";
import { ConversationTranscript } from "./transcript";
import { executionHeaders } from "./background-links";
import { ChildInputLabels, InputContent } from "./input-content";
import { workspaceTabs, emptyTabs } from "../native/workspace-tabs";

const live = vi.hoisted(() => ({ display: null as unknown, revision: 0 }));
vi.mock("./live-threads", () => ({ useLiveThread: () => live }));
afterEach(() => {
  cleanup();
  localStorage.clear();
});
const child = (id: string, state = "succeeded", segment = 0) =>
  ({
    execution_id: `exec-${id}-${segment}`,
    child_thread_id: id,
    parent_thread_id: "root",
    root_thread_id: "root",
    child_run_id: "run",
    subagent_name: `Agent ${id}`,
    segment_index: segment,
    persisted_status: state,
    local_status: state === "running" ? "active" : "unavailable",
    resumable: state === "succeeded",
    created_at: "2026-11-12T00:00:00Z",
    updated_at: "2026-11-12T00:00:01Z",
    available_actions: state === "running" ? ["steer", "cancel"] : [],
  }) as Schema<"ChildExecutionView">;
it("groups conversations across resumed segments, puts running first, and pages ended by 20", () => {
  const children = [
    child("running", "running"),
    child("running", "succeeded", -1),
    ...Array.from({ length: 24 }, (_, i) => child(String(i))),
  ];
  const items = backgroundItems("root", children, [], []);
  expect(items).toHaveLength(25);
  expect(items[0].child?.execution_id).toBe("exec-running-0");
  expect(backgroundGroups(items).running).toHaveLength(1);
  expect(backgroundGroups(items).ended).toHaveLength(20);
  expect(backgroundGroups(items).more).toBe(true);
  expect(backgroundGroups(items, 40).ended).toHaveLength(24);
});
it("uses stable child identity and deduplicates Shell execution observations", () => {
  expect(glyphIdentity("child-one")).toEqual(glyphIdentity("child-one"));
  expect(glyphIdentity("child-one").color).toBeLessThan(6);
  const tool = {
    execution_id: "exec-shell",
    tool_name: "shell_exec",
    foreground_released: true,
    status: "running",
    available_actions: ["cancel"],
  } as Schema<"ToolExecutionView">;
  expect(
    backgroundItems(
      "root",
      [],
      [tool],
      [
        {
          runId: "run",
          processId: "exec-shell",
          command: "pnpm dev",
          phase: "running",
          background: true,
        },
      ],
    ),
  ).toHaveLength(1);
  expect(
    backgroundItems("root", [], [{ ...tool, foreground_released: false }], []),
  ).toHaveLength(0);
});
function setup(
  children: Schema<"ChildExecutionView">[],
  tools: Schema<"ToolExecutionView">[] = [],
) {
  live.display = new FocusDisplay();
  const GET = vi.fn(async (path: string) => {
    if (path.endsWith("/children"))
      return { data: { executions: children, total: children.length } };
    if (path.endsWith("/tool-executions")) return { data: tools };
    if (path.endsWith("/output"))
      return { data: { state: "available", text: "result", truncated: false } };
    if (path.endsWith("/transcript"))
      return { data: { entries: [], turns: [], total: 0 } };
    if (path.endsWith("/inputs")) return { data: { turns: [] } };
    return {
      data: {
        thread: {
          thread_id: "root",
          configuration: { agent_source: { kind: "live" } },
        },
      },
    };
  });
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const open = vi.fn();
  const POST = vi.fn(async () => ({
    data: { accepted: true, execution_id: "exec-one-0" },
  }));
  const view = (id: string, content?: React.ReactNode) => (
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { GET, POST } } as unknown as Transport}
      >
        <MemoryRouter>
          <BackgroundScope threadId={id} onOpen={open}>
            <BackgroundToggle />
            <BackgroundCard />
            {content}
          </BackgroundScope>
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>
  );
  return { view, open, GET, POST };
}
it("stays closed as work arrives, remembers open per Thread, and only exposes permitted controls", async () => {
  const user = userEvent.setup();
  const children = [
    child("one", "running"),
    { ...child("uncontrolled", "running"), available_actions: [] },
  ];
  const test = setup(children);
  const page = render(test.view("root"));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Background work" }).textContent,
    ).toContain("2"),
  );
  expect(screen.queryByRole("region", { name: "Background work" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Background work" }));
  expect(screen.getAllByRole("button", { name: "Stop subagent" })).toHaveLength(
    1,
  );
  await user.click(screen.getByRole("button", { name: /Agent one/ }));
  expect(test.open).toHaveBeenCalledWith(
    expect.objectContaining({ id: "one", kind: "child" }),
  );
  page.rerender(test.view("another"));
  expect(screen.queryByRole("region", { name: "Background work" })).toBeNull();
  page.rerender(test.view("root"));
  expect(screen.getByRole("region", { name: "Background work" })).toBeTruthy();
});
it("keeps the row action separate from opening a resource tab", async () => {
  const test = setup([child("one", "running")]);
  render(test.view("root"));
  await userEvent.click(
    screen.getByRole("button", { name: "Background work" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Stop subagent" }),
  );
  expect(test.open).not.toHaveBeenCalled();
  expect(test.POST).toHaveBeenCalledWith(
    "/api/threads/{thread_id}/children/{execution_id}/cancel",
    expect.objectContaining({
      params: { path: { thread_id: "root", execution_id: "exec-one-0" } },
    }),
  );
});
it("shows more ended items on explicit activation", async () => {
  const test = setup(Array.from({ length: 24 }, (_, i) => child(String(i))));
  render(test.view("root"));
  await userEvent.click(
    screen.getByRole("button", { name: "Background work" }),
  );
  await screen.findByRole("button", { name: "Show more" });
  expect(screen.getAllByRole("button", { name: /Agent / })).toHaveLength(20);
  await userEvent.click(screen.getByRole("button", { name: "Show more" }));
  expect(screen.getAllByRole("button", { name: /Agent / })).toHaveLength(24);
});
it("distinguishes canonical notice kinds, preserves hidden source-marked rows and links executions", async () => {
  const test = setup([child("one", "running")]);
  const notices = [
    "Subagent message",
    "Subagent result",
    "Tool notification",
    "Tool result",
  ];
  const entries = notices.map((kind, i) => ({
    position: i,
    message_kind: "request",
    parts: [
      {
        kind: "user",
        text: `${kind} · Agent one · execution_id=exec-one-0 · running\nActual update`,
        metadata: {
          display: false,
          "a13n.steering-source": kind.startsWith("Tool")
            ? "tool_execution"
            : "async_subagent",
        },
      },
    ],
  })) as Schema<"TranscriptEntry">[];
  render(
    test.view(
      "root",
      <ConversationTranscript
        threadId="root"
        entries={entries}
        blocks={[]}
        localInputs={[]}
      />,
    ),
  );
  await waitFor(() =>
    expect(executionHeaders(entries[0].parts[0].text!)[0].kind).toBe(
      "Subagent message",
    ),
  );
  for (const kind of notices)
    expect(screen.getByText(kind, { exact: true })).toBeTruthy();
  for (const kind of notices)
    expect(screen.getByText(kind, { exact: true })).toBeTruthy();
  await waitFor(() =>
    expect(
      screen.getAllByRole("button", { name: "Open Agent one" }).length,
    ).toBeGreaterThan(0),
  );
  await userEvent.click(
    screen.getAllByRole("button", { name: "Open Agent one" })[0],
  );
  expect(test.open).toHaveBeenCalled();
});
it("labels parent inputs Main agent and unmarked child inputs User", () => {
  const test = setup([]);
  render(
    test.view(
      "root",
      <ChildInputLabels value={true}>
        <InputContent
          renderText={(text) => text}
          threadId="one"
          parts={[
            {
              kind: "user",
              text: "Parent task",
              metadata: { "a13n.steering-source": "parent_agent" },
            },
          ]}
        />
        <InputContent
          renderText={(text) => text}
          threadId="one"
          parts={[{ kind: "user", text: "Human steer" }]}
        />
      </ChildInputLabels>,
    ),
  );
  expect(screen.getByText("Main agent")).toBeTruthy();
  expect(screen.getByText("User")).toBeTruthy();
});
it("opens each background resource once in the existing tab strip", () => {
  const target = {
    kind: "child" as const,
    id: "one",
    threadId: "root",
    title: "Long child name",
  };
  const first = workspaceTabs(emptyTabs("new"), {
    type: "open",
    replace: true,
    tab: { id: "bg", kind: "background", target },
  });
  const second = workspaceTabs(first, {
    type: "open",
    replace: false,
    tab: { id: "duplicate", kind: "background", target },
  });
  expect(second.items).toHaveLength(1);
  expect(second.active).toBe("new");
});

it("shows Cancel only for API-authorized tools and keeps Shell observations read-only", async () => {
  const tool = {
    execution_id: "exec-tool",
    tool_call_id: "call",
    thread_id: "root",
    run_id: "run",
    tool_name: "report",
    status: "running",
    foreground_released: true,
    created_at: "2026-11-12T00:00:00Z",
    available_actions: ["cancel"],
  } as Schema<"ToolExecutionView">;
  const test = setup(
    [],
    [
      tool,
      { ...tool, execution_id: "exec-uncontrolled", available_actions: [] },
    ],
  );
  (live.display as FocusDisplay).processes.result(
    "run",
    "shell_start",
    { command: "pnpm test --watch" },
    { process_id: "process-observed", status: { phase: "running" } },
  );
  render(test.view("root"));
  await userEvent.click(
    screen.getByRole("button", { name: "Background work" }),
  );
  await screen.findByRole("button", { name: "Cancel execution" });
  expect(
    screen.getAllByRole("button", { name: "Cancel execution" }),
  ).toHaveLength(1);
  expect(screen.queryByRole("button", { name: "Stop subagent" })).toBeNull();
  expect(
    screen.getByRole("button", { name: /pnpm test --watch/ }),
  ).toBeTruthy();
});
it("omits a zero badge and distinguishes an empty card", async () => {
  const test = setup([]);
  render(test.view("root"));
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Background work" }).textContent,
    ).toBe(""),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Background work" }),
  );
  expect(await screen.findByText("Nothing is running.")).toBeTruthy();
});
