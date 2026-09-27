import { afterEach, expect, it, vi } from "vitest";
import { AppSession } from "./app-session";
import type { Schema, Transport } from "../transport/client";

const view: Schema<"AppView"> = {
  view_id: "view-one",
  connection_generation: "connection-one",
  root_thread_id: "thread-one",
  reference: {
    app_id: "app-one",
    thread_id: "thread-one",
    run_id: "run-one",
    tool_call_id: "call-one",
    server_id: "server-one",
    tool_name: "counter",
  },
};
function setup() {
  const POST = vi.fn();
  const GET = vi.fn();
  const PUT = vi.fn();
  const DELETE = vi.fn().mockResolvedValue({});
  const session = new AppSession(
    { client: { POST, GET, PUT, DELETE } } as unknown as Transport,
    view,
  );
  const operation = (
    key: string,
    status: Schema<"AppOperation">["status"],
  ): Schema<"AppOperation"> => ({
    operation_id: "operation-one",
    view_id: view.view_id,
    request_key: key,
    tool_id: "mcp/server-one/counter",
    name: "counter",
    arguments: { amount: 2 },
    status,
    result:
      status === "completed"
        ? { content: [], structuredContent: { count: 3 } }
        : undefined,
  });
  return { session, POST, GET, PUT, DELETE, operation };
}
afterEach(() => vi.useRealTimers());

it("reconciles a lost tool response by exact key without repeating its write", async () => {
  const { session, POST, GET, operation } = setup();
  POST.mockRejectedValue(new Error("response lost"));
  GET.mockImplementation((_url, { params }) =>
    Promise.resolve({ data: operation(params.path.request_key, "completed") }),
  );
  const args = { amount: 2 };
  const completion = session.call("counter", args);
  args.amount = 99;
  expect(await completion).toEqual({
    content: [],
    structuredContent: { count: 3 },
  });
  expect(POST).toHaveBeenCalledTimes(1);
  const body = POST.mock.calls[0][1].body;
  expect(body.arguments).toEqual({ amount: 2 });
  expect(GET.mock.calls[0][1].params.path.request_key).toBe(body.request_key);
  await session.close();
});

it("binds approval once and reconciles a lost decision response without resending", async () => {
  const { session, POST, GET, operation } = setup();
  POST.mockImplementationOnce((_url, { body }) =>
    Promise.resolve({ data: operation(body.request_key, "approval_required") }),
  );
  const completion = session.call("counter", { amount: 2 });
  await vi.waitFor(() =>
    expect(session.getSnapshot()[0].operation?.status).toBe(
      "approval_required",
    ),
  );
  const key = session.getSnapshot()[0].request.request_key;
  POST.mockRejectedValue(new Error("decision response lost"));
  GET.mockResolvedValue({ data: operation(key, "completed") });
  await Promise.all([session.decide(key, true), session.decide(key, true)]);
  expect((await completion).structuredContent).toEqual({ count: 3 });
  expect(POST).toHaveBeenCalledTimes(2);
  expect(POST.mock.calls[1][1].body).toEqual({ approve: true });
  await session.close();
});

it.each([true, false])(
  "never rebinds a submitted decision (%s) when reconciliation still awaits approval",
  async (approve) => {
    const { session, POST, GET, operation } = setup();
    POST.mockImplementationOnce((_url, { body }) =>
      Promise.resolve({
        data: operation(body.request_key, "approval_required"),
      }),
    );
    const completion = session.call("counter").catch((error: Error) => error);
    await vi.waitFor(() =>
      expect(session.getSnapshot()[0].operation?.status).toBe(
        "approval_required",
      ),
    );
    const key = session.getSnapshot()[0].request.request_key;
    POST.mockRejectedValue(new Error("decision response lost"));
    GET.mockResolvedValue({ data: operation(key, "approval_required") });
    await session.decide(key, approve);
    expect(session.getSnapshot()[0].uncertain).toContain(
      "Decision not confirmed",
    );
    await session.decide(key, !approve);
    await session.reconcile(key);
    await session.decide(key, approve);
    expect(POST).toHaveBeenCalledTimes(2);
    expect(POST.mock.calls[1][1].body).toEqual({ approve });
    await session.close();
    await completion;
  },
);

it("shows unknown outcomes, stops polling on connection failure, and only reads on Check result", async () => {
  vi.useFakeTimers();
  const { session, POST, GET, operation } = setup();
  POST.mockRejectedValue(new Error("offline"));
  GET.mockRejectedValue(new Error("offline"));
  const completion = session.call("counter");
  await vi.advanceTimersByTimeAsync(0);
  expect(session.getSnapshot()[0].uncertain).toContain("not repeated");
  await vi.advanceTimersByTimeAsync(60000);
  expect(GET).toHaveBeenCalledTimes(1);
  expect(POST).toHaveBeenCalledTimes(1);
  const key = session.getSnapshot()[0].request.request_key;
  GET.mockResolvedValue({ data: operation(key, "completed") });
  await session.reconcile(key);
  await completion;
  expect(POST).toHaveBeenCalledTimes(1);
  await session.close();
});

it("keeps only acknowledged latest context, requires selection and removes it on discard or close", async () => {
  const { session, PUT, DELETE } = setup();
  const first: Schema<"AppContext"> = {
    reference: { view_id: view.view_id, context_id: "one" },
    value: { structuredContent: { area: "north" } },
  };
  PUT.mockResolvedValueOnce({ data: first });
  await session.updateContext(first.value);
  expect(session.captureContext()).toBeUndefined();
  session.selectContext(true);
  expect(session.captureContext()).toEqual(first.reference);
  let resolve!: (value: unknown) => void;
  PUT.mockReturnValueOnce(
    new Promise((done) => {
      resolve = done;
    }),
  );
  const next = session.updateContext({ structuredContent: { area: "south" } });
  expect(() => session.captureContext()).toThrow("Wait for");
  await expect(session.updateContext({})).rejects.toThrow("pending");
  await vi.waitFor(() => expect(PUT).toHaveBeenCalledTimes(2));
  resolve({
    data: { ...first, reference: { ...first.reference, context_id: "two" } },
  });
  await next;
  expect(session.captureContext()?.context_id).toBe("two");
  await session.discardContext();
  expect(session.captureContext()).toBeUndefined();
  expect(DELETE.mock.calls[0][0]).toContain("/context");
  await session.close();
});

it("reads resources through only this View and validates the protocol response", async () => {
  const { session, POST } = setup();
  POST.mockResolvedValue({
    data: { contents: [{ uri: "data://selected", text: "selected" }] },
  });
  expect(
    (await session.readResource("data://selected")).contents[0],
  ).toMatchObject({ text: "selected" });
  expect(POST.mock.calls[0][1]).toMatchObject({
    body: { uri: "data://selected" },
    params: { path: { view_id: view.view_id } },
  });
  await session.close();
  await expect(session.readResource("data://selected")).rejects.toThrow(
    "closed",
  );
});

it("bounds request bytes and retained operations before posting or retaining work", async () => {
  const { session, POST, GET, operation } = setup();
  await expect(
    session.call("counter", { text: "界".repeat(90_000) }),
  ).rejects.toThrow("256 KiB");
  expect(session.getSnapshot()).toHaveLength(0);
  expect(POST).not.toHaveBeenCalled();
  POST.mockImplementation((_url, { body }) =>
    Promise.resolve({ data: operation(body.request_key, "completed") }),
  );
  for (let index = 0; index < 128; index++) await session.call("counter");
  await expect(session.call("counter")).rejects.toThrow("Reopen");
  expect(session.getSnapshot()).toHaveLength(128);
  expect(POST).toHaveBeenCalledTimes(128);
  expect(GET).not.toHaveBeenCalled();
  await session.close();
});

it("closing rejects the local waiter and closes only its View, not the MCP session", async () => {
  const { session, POST, DELETE, operation } = setup();
  POST.mockImplementation((_url, { body }) =>
    Promise.resolve({ data: operation(body.request_key, "approval_required") }),
  );
  const completion = session.call("counter").catch((error: Error) => error);
  await session.close();
  expect(((await completion) as Error).message).toContain("may still complete");
  expect(DELETE).toHaveBeenCalledWith(
    "/api/threads/{thread_id}/apps/{view_id}",
    expect.objectContaining({
      params: { path: expect.objectContaining({ view_id: view.view_id }) },
    }),
  );
  await expect(session.call("counter")).rejects.toThrow("closed");
  await session.close();
  expect(DELETE).toHaveBeenCalledTimes(1);
});
