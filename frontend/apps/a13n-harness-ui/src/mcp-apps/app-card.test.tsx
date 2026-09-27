// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import type { Schema, Transport } from "../transport/client";
import { ConversationTranscript } from "../conversations/transcript";
import { AppCard } from "./app-card";
import { AppContextProvider } from "./context-selection";
import type { AppHandlers } from "./app-frame";

vi.mock("./app-frame", () => ({
  AppFrame: ({ title, handlers }: { title: string; handlers: AppHandlers }) => (
    <>
      <iframe title={title} />
      <button
        onClick={() =>
          void handlers
            .onmessage?.(
              {
                role: "user",
                content: [{ type: "text", text: "Explain my selection" }],
              },
              {} as never,
            )
            .catch(() => {})
        }
      >
        Propose App message
      </button>
      <button
        onClick={() =>
          void handlers
            .onupdatemodelcontext?.(
              { content: [{ type: "text", text: "Selected area" }] },
              {} as never,
            )
            .catch(() => {})
        }
      >
        Update App context
      </button>
      <button
        onClick={() =>
          void handlers
            .oncalltool?.(
              { name: "counter", arguments: { amount: 2 } },
              {} as never,
            )
            .catch(() => {})
        }
      >
        Request from App
      </button>
    </>
  ),
}));
beforeEach(() =>
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener: vi.fn(),
    removeEventListener: vi.fn(),
  })),
);
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

const reference: Schema<"AppReference"> = {
  app_id: "app-one",
  thread_id: "thread-one",
  run_id: "run-one",
  tool_call_id: "call-one",
  server_id: "counter",
  tool_name: "counter",
};
const presentation: Schema<"AppPresentation"> = {
  reference,
  snapshot: {
    connection_generation: "conn-one",
    app_id: reference.app_id,
    thread_id: reference.thread_id,
    run_id: reference.run_id,
    tool_call_id: reference.tool_call_id,
    server_id: reference.server_id,
    tool: { name: "counter" },
    arguments: {},
    result: { content: [], structuredContent: { count: 1 } },
  },
  resource: {
    uri: "ui://counter/app.html",
    html: "<p>Counter</p>",
    metadata: {},
  },
  sandbox_url: "http://127.0.0.1:9001/sandbox.html",
};
function setup(value: Schema<"AppPresentation"> = presentation) {
  const POST = vi.fn().mockResolvedValue({ data: value });
  const GET = vi.fn();
  const PUT = vi.fn();
  const DELETE = vi.fn().mockResolvedValue({});
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const wrapper = ({ children }: { children: React.ReactNode }) => (
    <QueryClientProvider client={queries}>
      <TransportContext
        value={{ client: { POST, GET, PUT, DELETE } } as unknown as Transport}
      >
        <AppContextProvider>{children}</AppContextProvider>
      </TransportContext>
    </QueryClientProvider>
  );
  return { POST, GET, PUT, DELETE, wrapper };
}

it("opens history only on request and closing a view sends no server close or tool call", async () => {
  const { POST, wrapper } = setup();
  render(<AppCard reference={reference} />, { wrapper });
  expect(POST).not.toHaveBeenCalled();
  expect(screen.queryByTitle("counter")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Open App" }));
  await screen.findByTitle("counter");
  expect(POST).toHaveBeenCalledWith("/api/threads/{thread_id}/apps/open", {
    params: { path: { thread_id: reference.thread_id } },
    body: reference,
  });
  fireEvent.click(screen.getByRole("button", { name: "Close view" }));
  expect(screen.queryByTitle("counter")).toBeNull();
  expect(POST).toHaveBeenCalledTimes(1);
});

it("keeps one live App frame outside execution details when the original becomes saved", async () => {
  const { POST, wrapper } = setup();
  const input: Schema<"TranscriptEntry"> = {
    position: 0,
    message_kind: "request",
    parts: [
      {
        kind: "user",
        text: "Show counter",
        metadata: { source_id: "input-one" },
      },
    ],
  };
  const turns = [
    {
      turn_id: "input-one",
      input_position: 0,
      end_position: 3,
      preview: "Show counter",
    },
  ];
  const props = {
    threadId: reference.thread_id,
    entries: [input],
    turns,
    localInputs: [],
    continuation: "C0",
  };
  const view = render(
    <ConversationTranscript
      {...props}
      blocks={[
        {
          id: "run-one:call-one",
          toolCallId: "call-one",
          kind: "tool",
          name: "counter",
          text: "{}",
          done: true,
          apps: [reference],
        },
      ]}
    />,
    { wrapper },
  );
  const frame = await screen.findByTitle("counter");
  expect(frame.closest('[aria-label="Execution details"]')).toBeNull();
  view.rerender(
    <ConversationTranscript
      {...props}
      continuation="C1"
      blocks={[]}
      entries={[
        input,
        {
          position: 1,
          message_kind: "response",
          parts: [
            {
              kind: "tool_call",
              tool_name: "counter",
              tool_call_id: "call-one",
              value: {},
            },
          ],
        },
        {
          position: 2,
          message_kind: "request",
          parts: [
            {
              kind: "tool_result",
              tool_name: "counter",
              tool_call_id: "call-one",
              mcp_apps: [reference],
              value: { count: 1 },
            },
          ],
        },
      ]}
    />,
  );
  await waitFor(() => expect(screen.getByTitle("counter")).toBe(frame));
  expect(
    screen.getAllByRole("region", { name: "MCP App: counter" }),
  ).toHaveLength(1);
  expect(POST).toHaveBeenCalledTimes(1);
});

it("exposes inspect, opt-in selection and discard outside the frame without replacing it", async () => {
  const { POST, PUT, DELETE, wrapper } = setup();
  render(<AppCard reference={reference} live />, { wrapper });
  const frame = await screen.findByTitle("counter");
  POST.mockResolvedValueOnce({
    data: {
      view_id: "view-one",
      reference,
      connection_generation: "conn-one",
      root_thread_id: reference.thread_id,
    },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Activate interactions" }),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Activate interactions" }),
    ).toBeNull(),
  );
  PUT.mockResolvedValue({
    data: {
      reference: { view_id: "view-one", context_id: "context-one" },
      value: { content: [{ type: "text", text: "Selected area" }] },
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "Update App context" }));
  const selection = await screen.findByRole("checkbox", {
    name: "Include in my next messages",
  });
  expect((selection as HTMLInputElement).checked).toBe(false);
  expect(screen.getByText("Inspect App context").closest("iframe")).toBeNull();
  fireEvent.click(selection);
  expect((selection as HTMLInputElement).checked).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Discard context" }));
  await waitFor(() =>
    expect(DELETE).toHaveBeenCalledWith(
      "/api/threads/{thread_id}/apps/{view_id}/context",
      expect.anything(),
    ),
  );
  expect(screen.queryByRole("checkbox")).toBeNull();
  expect(screen.getByTitle("counter")).toBe(frame);
});

it("contains malformed original payload errors without crashing the conversation", async () => {
  const { wrapper } = setup({
    ...presentation,
    snapshot: { ...presentation.snapshot, result: { content: "invalid" } },
  });
  render(<AppCard reference={reference} live />, { wrapper });
  expect((await screen.findByRole("alert")).textContent).toContain(
    "cannot be displayed",
  );
  expect(screen.queryByTitle("counter")).toBeNull();
});

it("activates without replacing the frame and shows exact one-time approval outside the App", async () => {
  const { POST, DELETE, wrapper } = setup();
  render(<AppCard reference={reference} live />, { wrapper });
  const frame = await screen.findByTitle("counter");
  const view = {
    view_id: "view-one",
    reference,
    connection_generation: "connection-one",
    root_thread_id: reference.thread_id,
  };
  POST.mockResolvedValueOnce({ data: view });
  fireEvent.click(
    screen.getByRole("button", { name: "Activate interactions" }),
  );
  await screen.findByText(/Server interactions active/);
  expect(screen.getByTitle("counter")).toBe(frame);
  let operation: Schema<"AppOperation">;
  POST.mockImplementationOnce((_url, { body }) => {
    operation = {
      operation_id: "operation-one",
      view_id: view.view_id,
      request_key: body.request_key,
      tool_id: "mcp/counter/counter",
      name: "counter",
      arguments: body.arguments,
      status: "approval_required",
      reason: "This operation changes the counter.",
    };
    return Promise.resolve({ data: operation });
  });
  fireEvent.click(screen.getByRole("button", { name: "Request from App" }));
  const approval = await screen.findByRole("button", { name: "Approve once" });
  expect(screen.getByLabelText("Exact App arguments").textContent).toContain(
    '"amount": 2',
  );
  expect(screen.getByText("This operation changes the counter.")).toBeTruthy();
  expect(approval.closest("iframe")).toBeNull();
  POST.mockImplementationOnce(() =>
    Promise.resolve({
      data: {
        ...operation,
        status: "completed",
        result: { content: [], structuredContent: { count: 3 } },
      },
    }),
  );
  fireEvent.click(approval);
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Approve once" })).toBeNull(),
  );
  expect(screen.getByTitle("counter")).toBe(frame);
  expect(POST).toHaveBeenCalledTimes(4);
  fireEvent.click(screen.getByRole("button", { name: "Close view" }));
  expect(DELETE).toHaveBeenCalledTimes(1);
  expect(screen.queryByTitle("counter")).toBeNull();
});

it("requires a trusted one-time confirmation for a child App handoff without replacing its frame", async () => {
  const { POST, wrapper } = setup();
  render(<AppCard reference={reference} live />, { wrapper });
  const frame = await screen.findByTitle("counter");
  POST.mockResolvedValueOnce({
    data: {
      view_id: "view-one",
      reference,
      connection_generation: "conn-one",
      root_thread_id: "owning-root",
      route: ["researcher"],
    },
  });
  fireEvent.click(
    screen.getByRole("button", { name: "Activate interactions" }),
  );
  await screen.findByText(/Server interactions active/);
  POST.mockClear();
  fireEvent.click(screen.getByText("Propose App message"));
  const confirmation = await screen.findByRole("region", {
    name: "App message confirmation",
  });
  expect(confirmation.textContent).toContain("child App handoff");
  expect(confirmation.textContent).toContain("owning-root");
  expect(confirmation.textContent).toContain("Explain my selection");
  expect(POST).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Decline message"));
  expect(POST).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Propose App message"));
  POST.mockImplementationOnce((_url, { body }) =>
    Promise.resolve({
      data: {
        request: body,
        view_id: "view-one",
        root_thread_id: "owning-root",
        status: "accepted",
        receipt: {
          thread_id: "owning-root",
          receipt_id: "receipt-one",
          submitted_at: "2026-09-27T00:00:00Z",
        },
      },
    }),
  );
  fireEvent.click(screen.getByText("Send once"));
  await screen.findByText(/Accepted by the conversation/);
  expect(POST).toHaveBeenCalledExactlyOnceWith(
    "/api/threads/{thread_id}/apps/{view_id}/messages",
    expect.objectContaining({
      body: expect.objectContaining({
        content: [{ type: "text", text: "Explain my selection" }],
      }),
    }),
  );
  expect(screen.getByTitle("counter")).toBe(frame);
});
