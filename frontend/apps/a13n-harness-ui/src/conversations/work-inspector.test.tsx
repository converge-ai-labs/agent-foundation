// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import { FocusDisplay } from "./stream";
import { WorkInspector, TaskList } from "./work-inspector";
import { Child } from "./details";
import { ChildControlsProvider } from "./child-controls";

afterEach(cleanup);
const task = {
  task_id: "task-one",
  version: 1,
  subject: "Check output",
  status: "pending",
} satisfies Schema<"TaskView">;
function harness(GET: ReturnType<typeof vi.fn>, POST = vi.fn()) {
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: ReactNode }) => (
    <QueryClientProvider client={queries}>
      <TransportContext
        value={{ client: { GET, POST } } as unknown as Transport}
      >
        <ChildControlsProvider>{children}</ChildControlsProvider>
      </TransportContext>
    </QueryClientProvider>
  );
  return { wrapper, queries };
}
it("opens bounded task and saved-note inspection without editing the prompt", async () => {
  const GET = vi.fn(async (path: string) => ({
    data: path.endsWith("/children")
      ? { executions: [], total: 0 }
      : path.endsWith("/tasks")
        ? { tasks: [task], version: 1 }
        : {
            notes: [{ key: "Decision", value: "Keep the user's draft" }],
            omitted: 2,
          },
  }));
  const display = new FocusDisplay();
  display.baseContinuation = "C1";
  display.tasks = {
    version: 2,
    tasks: [
      {
        ...task,
        version: 2,
        status: "in_progress",
        active_form: "Checking output",
        blocked_by: ["task-before"],
        blocks: ["task-after"],
      },
    ],
  };
  const view = render(
    <WorkInspector
      threadId="root"
      continuation="C1"
      display={display}
      live
      reconcile={vi.fn()}
    />,
    harness(GET),
  );
  await screen.findByText("Checking output");
  expect(GET.mock.calls.some(([path]) => path.endsWith("/notes"))).toBe(false);
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Inspect tasks" }));
  const summary = screen
    .getAllByText("Checking output")
    .find((element) => element.tagName === "SUMMARY")!;
  fireEvent.click(summary);
  expect(screen.getByText("task-before")).toBeTruthy();
  expect(screen.getByText("task-after")).toBeTruthy();
  await user.keyboard("{Escape}");
  await user.click(screen.getByRole("button", { name: "Inspect notes" }));
  await screen.findByText("Decision");
  expect(screen.getByText(/Unsaved changes may not appear/)).toBeTruthy();
  expect(screen.getByText("2 notes omitted by the server.")).toBeTruthy();
  await user.keyboard("{Escape}");
  view.rerender(
    <WorkInspector
      threadId="root"
      continuation="C2"
      display={display}
      live={false}
      reconcile={vi.fn()}
    />,
  );
  await waitFor(() => expect(screen.queryByText("Checking output")).toBeNull());
});
it("orders active tasks first and distinguishes unavailable from empty", () => {
  const view = render(
    <TaskList
      page={{
        tasks: [
          task,
          { ...task, task_id: "done", subject: "Done", status: "completed" },
          {
            ...task,
            task_id: "active",
            subject: "Working",
            status: "in_progress",
          },
        ],
        omitted: 4,
      }}
    />,
  );
  expect(
    screen
      .getAllByRole("listitem")
      .map((item) => item.querySelector("summary")?.textContent),
  ).toEqual(["Working", "Check output", "Done"]);
  expect(screen.getByText("4 tasks outside this bounded view.")).toBeTruthy();
  view.rerender(<TaskList page={{ available: false }} />);
  expect(screen.getByText(/unavailable for this provider/)).toBeTruthy();
  expect(screen.queryByText("No tasks yet.")).toBeNull();
});
it("updates an open child's actual observed output and fetches saved output separately", async () => {
  const GET = vi.fn(async (path: string) => ({
    data: path.endsWith("/saved-output")
      ? { outputs: [] }
      : { title: "Execution record", value: {} },
  }));
  const child = {
    execution_id: "exec-one",
    root_thread_id: "root",
    parent_thread_id: "root",
    child_thread_id: "child",
    child_run_id: "run-child",
    subagent_name: "Explorer",
    persisted_status: "running",
    local_status: "active",
    segment_index: 0,
    created_at: "2026-09-14T00:00:00Z",
    activity: { sequence: 1, output_preview: "Earlier snapshot" },
    available_actions: [],
  } as unknown as Schema<"ChildExecutionView">;
  const live = new FocusDisplay();
  live.blocks.set("text", {
    id: "text",
    kind: "assistant",
    text: "Investigating",
  });
  const { wrapper, queries } = harness(GET);
  const view = render(<Child child={child} live={live} reconcile={vi.fn()} />, {
    wrapper,
  });
  fireEvent.click(screen.getByText("Explorer · running"));
  await screen.findByText("Investigating");
  expect(screen.getByText("Current observed output")).toBeTruthy();
  await waitFor(() =>
    expect(
      GET.mock.calls.some(([path]) => path.endsWith("/saved-output")),
    ).toBe(true),
  );
  live.blocks.set("text", {
    id: "text",
    kind: "assistant",
    text: "Investigating the realtime details",
  });
  view.rerender(<Child child={child} live={live} reconcile={vi.fn()} />);
  expect(screen.getByText("Investigating the realtime details")).toBeTruthy();
  await waitFor(() =>
    expect(
      GET.mock.calls.filter(([path]) => path.endsWith("/saved-output")),
    ).toHaveLength(1),
  );
  await queries.invalidateQueries({ queryKey: ["child-saved-output"] });
  await waitFor(() =>
    expect(
      GET.mock.calls.filter(([path]) => path.endsWith("/saved-output")),
    ).toHaveLength(2),
  );
  expect(screen.queryByRole("button", { name: "Stop child" })).toBeNull();
});

it("retains pending and unknown child controls when inspection unmounts and reopens", async () => {
  let reject!: (error: Error) => void;
  const POST = vi.fn(
    () =>
      new Promise((_resolve, fail) => {
        reject = fail;
      }),
  );
  const GET = vi.fn(async () => ({ data: { outputs: [] } }));
  const { wrapper } = harness(GET, POST);
  const child = {
    execution_id: "control-one",
    root_thread_id: "root",
    parent_thread_id: "root",
    subagent_name: "Worker",
    persisted_status: "running",
    local_status: "active",
    created_at: "2026-09-14T00:00:00Z",
    activity: {},
    available_actions: ["steer", "cancel"],
  } as Schema<"ChildExecutionView">;
  const view = render(<Child child={child} reconcile={vi.fn()} />, { wrapper });
  fireEvent.click(screen.getByText("Worker · running"));
  fireEvent.change(
    await screen.findByRole("textbox", { name: "Instruction for Worker" }),
    { target: { value: "Check once" } },
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Send child instruction" }),
  );
  await waitFor(() => expect(POST).toHaveBeenCalledOnce());
  view.rerender(<div>Closed</div>);
  view.rerender(<Child child={child} reconcile={vi.fn()} />);
  fireEvent.click(screen.getByText("Worker · running"));
  const input = await screen.findByRole("textbox", {
    name: "Instruction for Worker",
  });
  expect((input as HTMLInputElement).value).toBe("Check once");
  expect(
    (
      screen.getByRole("button", {
        name: "Send child instruction",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  reject(new TypeError("Acknowledgement lost"));
  await screen.findByText(/Acknowledgement unavailable/);
  view.rerender(<div>Closed again</div>);
  view.rerender(<Child child={child} reconcile={vi.fn()} />);
  fireEvent.click(screen.getByText("Worker · running"));
  await screen.findByText(/Acknowledgement unavailable/);
  expect(
    (
      screen.getByRole("textbox", {
        name: "Instruction for Worker",
      }) as HTMLInputElement
    ).value,
  ).toBe("Check once");
  fireEvent.click(
    screen.getByRole("button", { name: "Send child instruction" }),
  );
  expect(POST).toHaveBeenCalledOnce();
});

it("keeps completed output directly inspectable and tools collapsed without raw review events", async () => {
  const text =
    "A detailed finding.\n\n".repeat(600) + "Final review paragraph.";
  const GET = vi.fn(async (_path: string) => ({
    data: {
      outputs: [
        {
          target: {
            producing_thread_id: "child",
            source_id: "b".repeat(64),
            location: {
              kind: "child_text",
              execution_id: "long-review",
              activity: null,
            },
          },
          text,
          offset: 0,
          total_characters: text.length,
          next_offset: null,
        },
      ],
    },
  }));
  const child = {
    execution_id: "long-review",
    root_thread_id: "root",
    parent_thread_id: "root",
    subagent_name: "Reviewer",
    persisted_status: "succeeded",
    local_status: "unavailable",
    created_at: "2026-09-14T00:00:00Z",
    activity: {
      output_preview: "Short preview",
      recent_tool_calls: [
        {
          tool_call_id: "tool-1",
          tool_name: "view",
          status: "success",
          arguments: { file_path: "main.py" },
          result: "Source",
        },
      ],
    },
    available_actions: [],
  } as unknown as Schema<"ChildExecutionView">;
  render(<Child child={child} reconcile={vi.fn()} />, harness(GET));
  await userEvent.setup().click(screen.getByText("Reviewer · succeeded"));
  await screen.findByText("Final review paragraph.");
  expect(screen.queryByText("Short preview")).toBeNull();
  expect(screen.queryByText("Latest activity snapshot")).toBeNull();
  expect(screen.queryByText("Saved child output and comments")).toBeNull();
  expect(screen.getByText("Latest saved result")).toBeTruthy();
  expect(GET.mock.calls.every(([path]) => path.endsWith("/saved-output"))).toBe(
    true,
  );
  expect(
    screen
      .getByRole("button", { name: /Explored/ })
      .getAttribute("aria-expanded"),
  ).toBe("false");
});

it("inspects observed processes in place, updates status and discloses missing observations", async () => {
  const GET = vi.fn(async (path: string) => ({
    data: path.endsWith("/children")
      ? { executions: [], total: 0 }
      : { tasks: [] },
  }));
  const display = new FocusDisplay();
  const view = render(
    <WorkInspector
      threadId="root"
      display={display}
      live
      reconcile={vi.fn()}
    />,
    harness(GET),
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Inspect processes" }));
  expect(screen.getByText("No background processes observed.")).toBeTruthy();
  display.processes.result(
    "run-one",
    "shell_exec",
    { command: "pnpm dev" },
    { process_id: "process-one", status: { phase: "running" } },
  );
  view.rerender(
    <WorkInspector
      threadId="root"
      display={display}
      live
      reconcile={vi.fn()}
    />,
  );
  expect(screen.getByText("pnpm dev")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Inspect processes" }).textContent,
  ).toContain("1");
  fireEvent.click(screen.getByText("pnpm dev"));
  expect(screen.getByText("process-one")).toBeTruthy();
  expect(screen.getByText("run-one")).toBeTruthy();
  display.processes.status("run-one", "process-one", "exited", 3);
  view.rerender(
    <WorkInspector
      threadId="root"
      display={display}
      live
      connected={false}
      reconcile={vi.fn()}
    />,
  );
  expect(screen.getByText("Failed (exit 3)")).toBeTruthy();
  expect(screen.getByText(/Status may be stale/)).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Inspect processes" }).textContent,
  ).not.toContain("1");
  expect(GET.mock.calls.some(([path]) => path.includes("process"))).toBe(false);
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByText("pnpm dev")).toBeNull());
});
