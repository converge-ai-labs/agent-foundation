// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import {
  backgroundGroups,
  backgroundItems,
  GLYPH_COLORS,
  GLYPH_EYES,
  glyphIdentity,
} from "./background-work";
import { ChildControlsProvider } from "./child-controls";
import { BackgroundPanel } from "./background-panel";
import { BackgroundScope } from "./background-context";
import { BackgroundCard, BackgroundToggle } from "./background-card";
import { FocusDisplay } from "./stream";
import { ConversationTranscript } from "./transcript";
import { ToolActivity, ToolCall } from "./tool-call";
import { executionHeaders } from "./background-links";
import { ChildInputLabels, InputContent } from "./input-content";
import { workspaceTabs, emptyTabs } from "../native/workspace-tabs";

const t = (key: string) => key;
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
it("groups child conversations separately from tools and puts running first", () => {
  const children = [
    child("running", "running"),
    child("running", "succeeded", -1),
    ...Array.from({ length: 24 }, (_, i) => child(String(i))),
  ];
  const items = backgroundItems("root", children, [], [], t);
  expect(items).toHaveLength(25);
  expect(items[0].child?.execution_id).toBe("exec-running-0");
  expect(backgroundGroups(items)[0].items).toHaveLength(25);
  expect(backgroundGroups(items)[1].items).toHaveLength(0);
});
it("uses stable child identity and deduplicates Shell execution observations", () => {
  expect(glyphIdentity("child-one")).toEqual(glyphIdentity("child-one"));
  const glyph = glyphIdentity("child-one");
  expect(glyph.color).toBeLessThan(GLYPH_COLORS);
  expect(glyph.eyes).toBeLessThan(GLYPH_EYES);
  expect(glyph.path).toMatch(/^M.*Z$/);
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
      t,
    ),
  ).toHaveLength(1);
  expect(
    backgroundItems(
      "root",
      [],
      [{ ...tool, foreground_released: false }],
      [],
      t,
    ),
  ).toHaveLength(0);
});
function setup(
  children: Schema<"ChildExecutionView">[],
  tools: Schema<"ToolExecutionView">[] = [],
  closeOnOpen = false,
) {
  live.display = new FocusDisplay();
  const GET = vi.fn(
    async (
      path: string,
      _init?: { params?: { query?: Record<string, unknown> } },
    ): Promise<{ data: unknown }> => {
      if (path.endsWith("/children"))
        return { data: { executions: [...children], total: children.length } };
      if (path.endsWith("/tool-executions")) return { data: [...tools] };
      if (path.endsWith("/output"))
        return {
          data: { state: "available", text: "result", truncated: false },
        };
      if (path.endsWith("/transcript"))
        return {
          data: {
            entries: tools.map((tool, position) => ({
              position,
              message_kind: "request",
              parts: [
                {
                  kind: "tool_call",
                  tool_call_id: tool.tool_call_id,
                  tool_name: tool.tool_name,
                  value: { command: "pnpm test" },
                },
                {
                  kind: "tool_result",
                  tool_call_id: tool.tool_call_id,
                  tool_name: tool.tool_name,
                  value: {
                    type: "tool_execution_receipt",
                    execution_id: tool.execution_id,
                  },
                },
              ],
            })),
            turns: [],
            total: tools.length,
          },
        };
      if (path.endsWith("/inputs")) return { data: { turns: [] } };
      return {
        data: {
          thread: {
            thread_id: "root",
            configuration: { agent_source: { kind: "live" } },
          },
        },
      };
    },
  );
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const open = vi.fn();
  const POST = vi.fn(async (): Promise<{ data: unknown }> => ({
    data: { accepted: true, execution_id: "exec-one-0" },
  }));
  const view = (id: string, content?: React.ReactNode) => (
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { GET, POST } } as unknown as Transport}
      >
        <MemoryRouter>
          <ChildControlsProvider>
            <BackgroundScope
              threadId={id}
              onOpen={open}
              closeOnOpen={closeOnOpen}
            >
              <BackgroundToggle />
              <BackgroundCard />
              {content}
            </BackgroundScope>
          </ChildControlsProvider>
        </MemoryRouter>
      </TransportContext>
    </QueryClientProvider>
  );
  return { view, open, GET, POST, query };
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
it("limits each card section to three rows and opens its own history tab", async () => {
  const test = setup([
    child("running", "running"),
    ...Array.from({ length: 24 }, (_, i) => child(String(i))),
  ]);
  const page = render(test.view("root"));
  await userEvent.click(
    screen.getByRole("button", { name: "Background work" }),
  );
  await screen.findByRole("button", { name: "View all" });
  expect(screen.getAllByRole("button", { name: /Agent / })).toHaveLength(3);
  expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  await userEvent.click(screen.getByRole("button", { name: "View all" }));
  expect(test.open).toHaveBeenCalledWith({
    kind: "subagents",
    id: "root",
    threadId: "root",
    title: "Subagents",
  });
  await userEvent.click(
    screen.getByRole("button", { name: "Close background work" }),
  );
  page.rerender(
    test.view(
      "root",
      <BackgroundPanel
        target={{
          kind: "subagents",
          id: "root",
          threadId: "root",
          title: "Subagents",
        }}
      />,
    ),
  );
  const finished = await screen.findByRole("button", { name: /Finished/ });
  expect(screen.getAllByRole("button", { name: /Agent / })).toHaveLength(1);
  await userEvent.click(finished);
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: /Agent / })).toHaveLength(25),
  );
});
it("distinguishes canonical notice kinds, preserves source-marked rows and links executions", async () => {
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
          display: true,
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
    expect(screen.getAllByRole("button", { name: "Agent one" }).length).toBe(4),
  );
  await userEvent.click(
    screen.getAllByRole("button", { name: "Agent one" })[0],
  );
  expect(test.open).toHaveBeenCalled();
});
it("opens a handed-off shell call from its single inline action before the activity list loads it", async () => {
  const test = setup([]);
  render(
    test.view(
      "root",
      <ToolActivity
        tools={[
          {
            id: "launch",
            name: "shell_exec",
            input: { command: "sleep 60" },
            result: {
              type: "tool_execution_receipt",
              execution_id: "exec-shell",
              tool_name: "shell_exec",
              status: "running",
            },
          },
        ]}
      />,
    ),
  );
  const action = screen.getByRole("button", { name: "Moved to background" });
  expect(screen.getAllByText("Moved to background")).toHaveLength(1);
  expect(action.closest('[data-slot="disclosure-heading"]')).toBeTruthy();
  await userEvent.click(action);
  expect(test.open).toHaveBeenCalledWith(
    expect.objectContaining({
      kind: "tool",
      id: "exec-shell",
      threadId: "root",
    }),
  );
});
it("opens the spawned child by mouse or keyboard without expanding the tool details", async () => {
  const test = setup([]);
  render(
    test.view(
      "root",
      <ToolCall
        tool={{
          id: "spawn",
          name: "spawn_subagent",
          input: { name: "Reviewer" },
          result: {
            thread_id: "child-one",
            execution_id: "exec-one",
            subagent_name: "Reviewer",
          },
        }}
      />,
    ),
  );
  const child = screen.getByRole("button", { name: "Reviewer" });
  const trigger = screen.getByRole("button", { name: /Start subagent/ });
  await userEvent.click(child);
  await userEvent.keyboard("{Enter}");
  expect(test.open).toHaveBeenCalledTimes(2);
  expect(test.open).toHaveBeenCalledWith(
    expect.objectContaining({
      kind: "child",
      id: "child-one",
      threadId: "root",
    }),
  );
  expect(trigger.getAttribute("aria-expanded")).toBe("false");
  await userEvent.click(trigger);
  expect(trigger.getAttribute("aria-expanded")).toBe("true");
});
it("links a retained-file notice from an earlier segment using its execution metadata", async () => {
  const test = setup([child("one", "running", 1)]);
  const read = test.GET.getMockImplementation()!;
  test.GET.mockImplementation(async (path, init) =>
    init?.params?.query?.execution_id === "exec-one-0"
      ? { data: { executions: [child("one", "succeeded", 0)], total: 1 } }
      : read(path, init),
  );
  const entries = [
    {
      position: 0,
      message_kind: "request",
      parts: [
        {
          kind: "user",
          text: "The submitted text is saved in a retained input file.",
          metadata: {
            display: true,
            "a13n.steering-source": "async_subagent",
            execution_ids: ["exec-one-0"],
          },
        },
      ],
    },
  ] as Schema<"TranscriptEntry">[];
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
  await userEvent.click(
    await screen.findByRole("button", { name: "Agent one" }),
  );
  expect(test.open).toHaveBeenCalledWith(
    expect.objectContaining({ kind: "child", id: "one" }),
  );
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

it("closes the card when opening a resource in the narrow layout without fetching tool output per row", async () => {
  const test = setup(
    [],
    [
      {
        execution_id: "exec-tool",
        tool_call_id: "call-tool",
        tool_name: "shell_exec",
        foreground_released: true,
        status: "running",
        available_actions: ["cancel"],
      } as Schema<"ToolExecutionView">,
    ],
    true,
  );
  render(test.view("root"));
  await userEvent.click(
    screen.getByRole("button", { name: "Background work" }),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: /^pnpm test/ }),
  );
  expect(test.open).toHaveBeenCalled();
  expect(screen.queryByRole("region", { name: "Background work" })).toBeNull();
  expect(test.GET.mock.calls.some(([path]) => path.endsWith("/output"))).toBe(
    false,
  );
});
it("makes the child record read-only and keeps an accepted Stop pending until terminal", async () => {
  const records = [child("one", "running")];
  const test = setup(records);
  render(
    test.view(
      "root",
      <BackgroundPanel
        target={{
          kind: "child",
          threadId: "root",
          id: "one",
          title: "Agent one",
        }}
      />,
    ),
  );
  await userEvent.click(
    await screen.findByRole("button", { name: "Stop subagent" }),
  );
  expect(await screen.findByRole("button", { name: "Stopping…" })).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  records[0] = {
    ...records[0],
    persisted_status: "cancelled",
    available_actions: [],
    updated_at: "2026-11-12T00:00:02Z",
  };
  await act(async () => {
    await test.query.invalidateQueries({
      queryKey: ["thread", "root", "children"],
    });
  });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Stopping…" })).toBeNull(),
  );
  expect(screen.getByRole("img", { name: "Cancelled" })).toBeTruthy();
});

it("reads the terminal child checkpoint when its head settles after a running record", async () => {
  const running = child("one", "running");
  const children = [running];
  const test = setup(children);
  const read = test.GET.getMockImplementation()!;
  test.GET.mockImplementation(async (path: string) => {
    const terminal = children[0].persisted_status === "succeeded";
    const continuation_id = terminal
      ? "terminal-checkpoint"
      : "request-checkpoint";
    if (path === "/api/threads/{thread_id}")
      return {
        data: {
          thread: {
            thread_id: "one",
            configuration: { agent_source: { kind: "live" } },
          },
          continuation_id,
        },
      };
    if (path.endsWith("/transcript"))
      return {
        data: {
          continuation_id,
          entries: [
            {
              position: 0,
              message_kind: "response",
              parts: [
                {
                  kind: "assistant",
                  text: terminal
                    ? "Final child report"
                    : "Partial child report",
                },
              ],
            },
          ],
          turns: [],
          total: 1,
        },
      };
    return read(path);
  });
  render(
    test.view(
      "root",
      <BackgroundPanel
        target={{
          kind: "child",
          threadId: "root",
          id: "one",
          title: "Agent one",
        }}
      />,
    ),
  );
  await screen.findByText("Partial child report");
  children[0] = {
    ...running,
    persisted_status: "succeeded",
    local_status: "unavailable",
    updated_at: "2026-11-12T00:00:02Z",
  };
  await act(async () => {
    await test.query.invalidateQueries({
      queryKey: ["thread", "root", "children"],
    });
  });
  await screen.findByText("Final child report");
  expect(screen.queryByText("Partial child report")).toBeNull();
});

it("uses the title alone without a preview and never queries child inputs for tooltips", async () => {
  const test = setup([child("one", "running")]);
  render(test.view("root"));
  await userEvent.click(
    screen.getByRole("button", { name: "Background work" }),
  );
  const row = await screen.findByRole("button", { name: /Agent one/ });
  expect(within(row).getByText("Agent one").title).toBe("Agent one");
  await userEvent.hover(within(row).getByRole("img", { name: "Running" }));
  expect(await screen.findByText("Running")).toBeTruthy();
  expect(test.GET.mock.calls.some(([path]) => path.endsWith("/inputs"))).toBe(
    false,
  );
});

it("shares server tool cancellation progress across the card, history and item header", async () => {
  const tools = [
    {
      execution_id: "exec-tool",
      thread_id: "root",
      tool_call_id: "call-tool",
      tool_name: "shell_exec",
      foreground_released: true,
      status: "running",
      cancel_requested: false,
      available_actions: ["cancel"],
    } as Schema<"ToolExecutionView">,
  ];
  const test = setup([], tools);
  test.POST.mockImplementation(async () => {
    tools[0] = { ...tools[0], cancel_requested: true };
    return { data: tools[0] };
  });
  render(
    test.view(
      "root",
      <>
        <BackgroundPanel
          target={{
            kind: "tools",
            id: "root",
            threadId: "root",
            title: "Tool calls",
          }}
        />
        <BackgroundPanel
          target={{
            kind: "tool",
            id: "exec-tool",
            threadId: "root",
            title: "Shell",
          }}
        />
      </>,
    ),
  );
  await userEvent.click(
    screen.getByRole("button", { name: "Background work" }),
  );
  await userEvent.click(
    (await screen.findAllByRole("button", { name: "Cancel execution" }))[0],
  );
  await waitFor(() =>
    expect(screen.getAllByRole("button", { name: "Stopping…" })).toHaveLength(
      3,
    ),
  );
  tools[0] = { ...tools[0], status: "cancelled", available_actions: [] };
  await act(async () => {
    await test.query.invalidateQueries({
      queryKey: ["thread", "root", "tool-executions"],
    });
  });
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Stopping…" })).toBeNull(),
  );
  expect(
    screen.getAllByRole("img", { name: "Cancelled" }).length,
  ).toBeGreaterThan(0);
});

it("requests another history page only after Show more", async () => {
  const test = setup([]);
  const read = test.GET.getMockImplementation()!;
  let pages = 0;
  test.GET.mockImplementation(async (path) => {
    if (!path.endsWith("/children")) return read(path);
    pages += 1;
    return {
      data: {
        executions: [child(pages === 1 ? "first" : "older")],
        total: 2,
        next_cursor: pages === 1 ? "older-page" : null,
      },
    };
  });
  render(
    test.view(
      "root",
      <BackgroundPanel
        target={{
          kind: "subagents",
          id: "root",
          threadId: "root",
          title: "Subagents",
        }}
      />,
    ),
  );
  const toggle = await screen.findByRole("button", { name: /Finished/ });
  expect(screen.queryByRole("button", { name: /Agent first/ })).toBeNull();
  expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
  await userEvent.click(toggle);
  await screen.findByRole("button", { name: /Agent first/ });
  expect(pages).toBe(1);
  const query = test.GET.mock.calls.find(([path]) =>
    path.endsWith("/children"),
  )?.[1]?.params?.query;
  // The server orders one latest execution per child: running, then newest.
  expect(query).toMatchObject({ latest: true, limit: 100 });
  await userEvent.click(screen.getByRole("button", { name: "Show more" }));
  await screen.findByRole("button", { name: /Agent older/ });
  expect(pages).toBe(2);
  expect(screen.queryByRole("button", { name: "Show more" })).toBeNull();
});
