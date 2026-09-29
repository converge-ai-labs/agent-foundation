// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import type { ReactNode } from "react";
import { TransportContext } from "../transport/context";
import { ApiError, type Schema, type Transport } from "../transport/client";
import { ThreadSelections } from "./configuration";
import { DecisionForm } from "./decisions";
import { Child } from "./details";

afterEach(cleanup);
function harness(client: object) {
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  return function Wrapper({ children }: { children: ReactNode }) {
    return (
      <QueryClientProvider client={queries}>
        <TransportContext value={{ client } as Transport}>
          {children}
        </TransportContext>
      </QueryClientProvider>
    );
  };
}
const configuration: Schema<"ThreadConfiguration"> = {
  version: 1,
  agent_source: { kind: "agent", id: "agent-one" },
  environment_profile_id: "local",
};
it("keeps dirty selections on external changes and requires review before versioned apply", async () => {
  const PATCH = vi.fn().mockResolvedValue({
    data: {
      thread_id: "one",
      metadata_version: 1,
      configuration: { ...configuration, version: 3 },
    },
  });
  const wrapper = harness({
    PATCH,
    GET: vi.fn(async (path) => ({
      data:
        path === "/api/projects"
          ? []
          : {
              agents: [],
              environments: [],
              mcp_servers: [{ resource_id: "mcp-one", name: "Tools" }],
            },
    })),
  });
  const view = render(
    <ThreadSelections threadId="one" configuration={configuration} />,
    { wrapper },
  );
  fireEvent.click(screen.getByText("Change next Run selections"));
  const checkbox = await screen.findByRole("checkbox", { name: "Tools" });
  fireEvent.click(checkbox);
  view.rerender(
    <ThreadSelections
      threadId="one"
      configuration={{
        ...configuration,
        version: 2,
        agent_source: { kind: "agent", id: "other-agent" },
      }}
    />,
  );
  expect((checkbox as HTMLInputElement).checked).toBe(true);
  expect(
    (
      screen.getByRole("button", {
        name: "Save next Run selections",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(
    screen.getByText(/Your edits are retained against version 1/),
  ).toBeTruthy();
  fireEvent.click(
    screen.getByRole("button", { name: "Keep my edits against version 2" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Save next Run selections" }),
  );
  await waitFor(() => expect(PATCH).toHaveBeenCalledOnce());
  expect(PATCH.mock.calls[0][1].body).toEqual({
    expected_version: 2,
    patch: { mcp_server_ids: ["mcp-one"] },
  });
});
it("clears a saved default Model with an explicit null versioned patch", async () => {
  const user = userEvent.setup();
  const PATCH = vi.fn().mockResolvedValue({
    data: {
      thread_id: "one",
      metadata_version: 1,
      configuration: { ...configuration, version: 2, default_model_id: null },
    },
  });
  const wrapper = harness({
    PATCH,
    GET: vi.fn(async (path) => ({
      data:
        path === "/api/projects"
          ? []
          : {
              agents: [],
              environments: [],
              models: [
                {
                  model_id: "model-one",
                  name: "Saved model",
                  route: "custom:one",
                },
              ],
            },
    })),
  });
  render(
    <ThreadSelections
      threadId="one"
      configuration={{ ...configuration, default_model_id: "model-one" }}
    />,
    { wrapper },
  );
  fireEvent.click(screen.getByText("Change next Run selections"));
  const choice = await screen.findByRole("combobox", { name: "Default model" });
  await waitFor(() => expect(choice.textContent).toBe("Saved model"));
  await user.click(choice);
  await user.click(
    await screen.findByRole("option", { name: "Follow Agent model" }),
  );
  await user.click(
    screen.getByRole("button", { name: "Save next Run selections" }),
  );
  await waitFor(() => expect(PATCH).toHaveBeenCalledOnce());
  expect(PATCH.mock.calls[0][1].body).toEqual({
    expected_version: 1,
    patch: { default_model_id: null },
  });
  // The parent inspection still holds version 1; the acknowledgement owns the
  // visible value and the next write's version without another GET.
  await waitFor(() => expect(choice.textContent).toBe("Follow Agent model"));
  await user.click(choice);
  await user.click(await screen.findByRole("option", { name: "Saved model" }));
  await user.click(
    screen.getByRole("button", { name: "Save next Run selections" }),
  );
  await waitFor(() => expect(PATCH).toHaveBeenCalledTimes(2));
  expect(PATCH.mock.calls[1][1].body).toEqual({
    expected_version: 2,
    patch: { default_model_id: "model-one" },
  });
});

const batch = {
  thread_id: "one",
  continuation_id: "C1",
  requests: [
    {
      kind: "question",
      tool_name: "ask",
      request_id: "question-one",
      questions: [
        {
          question: "Which direction?",
          header: "Direction",
          multi_select: false,
          options: [
            { label: "Left", description: "Choose left" },
            { label: "Right", description: "Choose right" },
          ],
        },
      ],
    },
  ],
} as Schema<"DecisionBatchView">;
it("sends a complete decision set keyed by question text and does not retry an unknown acknowledgement", async () => {
  const POST = vi.fn().mockRejectedValue(new TypeError("Connection lost"));
  render(<DecisionForm threadId="one" batch={batch} reconcile={vi.fn()} />, {
    wrapper: harness({ POST }),
  });
  expect(
    (
      screen.getByRole("button", {
        name: "Submit responses",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(false);
  fireEvent.click(screen.getByRole("radio", { name: "Left Choose left" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit responses" }));
  await screen.findByText(/Acknowledgement unavailable/);
  expect(POST.mock.calls[0][1].body).toEqual({
    expected_continuation_id: "C1",
    responses: [
      {
        kind: "question",
        request_id: "question-one",
        answers: { "Which direction?": "Left" },
      },
    ],
  });
  fireEvent.click(screen.getByRole("button", { name: "Submit responses" }));
  expect(POST).toHaveBeenCalledOnce();
});
it("invalidating a previously complete answer prevents stale submission", () => {
  const POST = vi.fn();
  render(<DecisionForm threadId="one" batch={batch} reconcile={vi.fn()} />, {
    wrapper: harness({ POST }),
  });
  fireEvent.click(screen.getByRole("radio", { name: "Left Choose left" }));
  fireEvent.change(
    screen.getByRole("textbox", { name: "Or write your own answer" }),
    { target: { value: "other" } },
  );
  fireEvent.change(
    screen.getByRole("textbox", { name: "Or write your own answer" }),
    { target: { value: "" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Submit responses" }));
  expect(POST).not.toHaveBeenCalled();
});
it("keeps child instructions after unknown control and addresses the exact parent execution", async () => {
  const POST = vi
    .fn()
    .mockRejectedValue(new ApiError("Proxy unavailable", 502));
  const child = {
    execution_id: "child-one",
    root_thread_id: "root",
    parent_thread_id: "parent",
    subagent_name: "Explorer",
    persisted_status: "running",
    local_status: "active",
    segment_index: 0,
    child_thread_id: "child-thread",
    child_run_id: "child-run",
    composition_id: "composition",
    child_definition_id: "explorer",
    resumable: false,
    created_at: "2026-09-12T00:00:00Z",
    updated_at: "2026-09-12T00:00:00Z",
    activity: {},
    available_actions: ["steer", "cancel"],
  } as Schema<"ChildExecutionView">;
  render(<Child child={child} reconcile={vi.fn()} />, {
    wrapper: harness({
      POST,
      GET: vi.fn().mockResolvedValue({ data: { outputs: [] } }),
    }),
  });
  fireEvent.click(screen.getByText("Explorer · running"));
  fireEvent.change(
    await screen.findByRole("textbox", { name: "Instruction for Explorer" }),
    { target: { value: "Inspect this" } },
  );
  fireEvent.click(screen.getByRole("button", { name: "Guide subagent" }));
  await screen.findByText(/Acknowledgement unavailable/);
  expect(POST.mock.calls[0]).toEqual([
    "/api/threads/{thread_id}/children/{execution_id}/steer",
    {
      params: { path: { thread_id: "parent", execution_id: "child-one" } },
      body: { prompt: "Inspect this" },
    },
  ]);
  expect(
    (
      screen.getByRole("textbox", {
        name: "Instruction for Explorer",
      }) as HTMLInputElement
    ).value,
  ).toBe("Inspect this");
  fireEvent.click(screen.getByRole("button", { name: "Guide subagent" }));
  expect(POST).toHaveBeenCalledOnce();
});

it("disables selection editing during the submitted versioned write", async () => {
  const PATCH = vi.fn(() => new Promise(() => {}));
  const wrapper = harness({
    PATCH,
    GET: vi.fn(async (path) => ({
      data:
        path === "/api/projects"
          ? []
          : {
              agents: [],
              environments: [],
              mcp_servers: [
                { resource_id: "a", name: "A" },
                { resource_id: "b", name: "B" },
              ],
            },
    })),
  });
  render(<ThreadSelections threadId="one" configuration={configuration} />, {
    wrapper,
  });
  fireEvent.click(screen.getByText("Change next Run selections"));
  fireEvent.click(await screen.findByRole("checkbox", { name: "A" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Save next Run selections" }),
  );
  await waitFor(() => expect(PATCH).toHaveBeenCalledOnce());
  expect(screen.getByRole("checkbox", { name: "B" }).matches(":disabled")).toBe(
    true,
  );
  expect(
    screen.getByRole("combobox", { name: "Agent" }).matches(":disabled"),
  ).toBe(true);
});
it("submits an external denial with a valid default reason, and no invented result", async () => {
  const POST = vi
    .fn()
    .mockResolvedValue({ data: { receipt_id: "receipt-one" } });
  render(
    <DecisionForm
      threadId="one"
      reconcile={vi.fn()}
      batch={{
        continuation_id: "C1",
        requests: [
          {
            kind: "external",
            request_id: "external-one",
            tool_name: "Review",
            arguments: {},
          },
        ],
      }}
    />,
    { wrapper: harness({ POST }) },
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "External result" }));
  await user.click(await screen.findByRole("option", { name: "Deny" }));
  fireEvent.click(screen.getByRole("button", { name: "Submit responses" }));
  await waitFor(() => expect(POST).toHaveBeenCalledOnce());
  expect(POST.mock.calls[0][1].body.responses).toEqual([
    {
      kind: "external",
      request_id: "external-one",
      denied: true,
      denial_message: "Denied.",
    },
  ]);
});
it("validates an approval override as a JSON object before allowing the complete response", async () => {
  const POST = vi
    .fn()
    .mockResolvedValue({ data: { receipt_id: "receipt-one" } });
  render(
    <DecisionForm
      threadId="one"
      reconcile={vi.fn()}
      batch={{
        continuation_id: "C1",
        requests: [
          {
            kind: "approval",
            request_id: "approval-one",
            tool_name: "Run",
            arguments: { command: "inspect" },
            override_allowed: true,
          },
        ],
      }}
    />,
    { wrapper: harness({ POST }) },
  );
  fireEvent.click(screen.getByRole("button", { name: "Edit arguments" }));
  const input = screen.getByRole("textbox", {
    name: "Replacement arguments (JSON object)",
  });
  fireEvent.change(input, { target: { value: "[]" } });
  expect(
    (
      screen.getByRole("button", {
        name: "Approve with edited arguments",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  fireEvent.change(input, { target: { value: '{"command":"review"}' } });
  fireEvent.click(
    screen.getByRole("button", { name: "Approve with edited arguments" }),
  );
  await waitFor(() => expect(POST).toHaveBeenCalledOnce());
  expect(POST.mock.calls[0][1].body.responses).toEqual([
    {
      kind: "approval",
      request_id: "approval-one",
      approved: true,
      override_arguments: { command: "review" },
    },
  ]);
});

it("shows the server deadline despite clock skew and refetches at expiry without submitting drafts", () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "performance"] });
  const POST = vi.fn();
  const reconcile = vi.fn();
  const timedBatch = {
    ...batch,
    server_time: "2020-01-01T00:00:00Z",
    expires_at: "2020-01-01T00:00:02Z",
  };
  const view = render(
    <DecisionForm threadId="one" batch={timedBatch} reconcile={reconcile} />,
    { wrapper: harness({ POST }) },
  );
  try {
    expect(screen.getByText(/Submit within 2s/)).toBeTruthy();
    fireEvent.click(screen.getByRole("radio", { name: "Left Choose left" }));
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByText(/Submit within 1s/)).toBeTruthy();
    // A second participant/refetch sees the same deadline, not another full window.
    view.rerender(
      <DecisionForm
        threadId="one"
        batch={{ ...timedBatch, server_time: "2020-01-01T00:00:01Z" }}
        reconcile={reconcile}
      />,
    );
    act(() => vi.advanceTimersByTime(1000));
    expect(screen.getByText(/Waiting for the server to confirm/)).toBeTruthy();
    expect(
      (
        screen.getByRole("button", {
          name: "Submit responses",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(true);
    expect(reconcile).toHaveBeenCalledOnce();
    expect(POST).not.toHaveBeenCalled();
    act(() => vi.advanceTimersByTime(1000));
    expect(reconcile).toHaveBeenCalledOnce();
  } finally {
    view.unmount();
    vi.useRealTimers();
  }
});

it("unmounting a timed form never sends a response and a restored untimed batch stays answerable", () => {
  vi.useFakeTimers({ toFake: ["setInterval", "clearInterval", "performance"] });
  const POST = vi.fn();
  const reconcile = vi.fn();
  const wrapper = harness({ POST });
  const view = render(
    <DecisionForm
      threadId="one"
      batch={{
        ...batch,
        server_time: "2020-01-01T00:00:00Z",
        expires_at: "2020-01-01T00:00:01Z",
      }}
      reconcile={reconcile}
    />,
    { wrapper },
  );
  view.unmount();
  try {
    act(() => vi.advanceTimersByTime(2000));
    expect(POST).not.toHaveBeenCalled();
    expect(reconcile).not.toHaveBeenCalled();
    const restored = render(
      <DecisionForm threadId="one" batch={batch} reconcile={reconcile} />,
      { wrapper },
    );
    expect(screen.queryByText(/Submit within/)).toBeNull();
    fireEvent.click(screen.getByRole("radio", { name: "Left Choose left" }));
    expect(
      (
        screen.getByRole("button", {
          name: "Submit responses",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false);
    restored.unmount();
  } finally {
    vi.useRealTimers();
  }
});

it("edits Run-only local and remote selections without changing the Thread", async () => {
  const { RunEnvironments } = await import("./thread-run-choices");
  const onChange = vi.fn();
  const wrapper = harness({ GET: vi.fn(async () => ({ data: [] })) });
  render(
    <RunEnvironments
      configuration={{
        ...configuration,
        local_roots: ["/old"],
        environment_bindings: [
          {
            device_id: "device-build",
            alias: "build",
            working_directory: "/remote",
          },
        ],
        default_environment: "build",
      }}
      onChange={onChange}
      disabled={false}
    />,
    { wrapper },
  );
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", {
      name: "Environments",
      description: "2 environments",
    }),
  );
  const path = screen.getByRole("textbox", { name: "Server directory" });
  await user.clear(path);
  await user.type(path, "/selected");
  expect(onChange).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Use for next Run" }));
  expect(onChange).toHaveBeenCalledWith({
    local_roots: ["/selected"],
    environment_bindings: [
      {
        device_id: "device-build",
        alias: "build",
        working_directory: "/remote",
      },
    ],
    default_environment: "build",
  });
  await user.click(screen.getByRole("button", { name: "Environments" }));
  await user.click(
    screen.getByRole("button", { name: "Use conversation defaults" }),
  );
  expect(onChange).toHaveBeenLastCalledWith(undefined);
});
