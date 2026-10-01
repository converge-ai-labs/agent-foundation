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
import { McpInputs } from "./mcp-inputs";
import { ApiError } from "../transport/client";

const client = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../transport/context", () => ({ useTransport: () => ({ client }) }));
const request = {
  request_id: "mcpinp-one",
  thread_id: "child-one",
  server_id: "mcp-input",
  run_id: "run-one",
  tool_call_id: "call-one",
  mode: "form",
  message: "Confirm this operation",
  schema: {
    type: "object",
    properties: {
      name: { type: "string", title: "Name" },
      choices: {
        type: "array",
        items: {
          anyOf: [
            { const: "a", title: "Alpha" },
            { const: "b", title: "Beta" },
          ],
        },
      },
    },
    required: ["name"],
  },
  state: "pending",
  expires_at: "2026-10-01T20:00:00Z",
};
function mount() {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <McpInputs threadId="root-one" />
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  vi.resetAllMocks();
  client.GET.mockResolvedValue({ data: [request] });
});
afterEach(cleanup);

it("answers a child-owned form through its input endpoint without submitting another Run", async () => {
  client.POST.mockImplementation(async () => {
    client.GET.mockResolvedValue({ data: [{ ...request, state: "accepted" }] });
    return { data: { ...request, state: "accepted" } };
  });
  mount();
  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: "Ada" },
  });
  expect(screen.getByText("From child Thread child-one")).toBeTruthy();
  fireEvent.click(screen.getByLabelText("Alpha"));
  fireEvent.click(screen.getByRole("button", { name: "Send response" }));
  await waitFor(() => expect(client.POST).toHaveBeenCalledTimes(1));
  expect(client.POST).toHaveBeenCalledWith(
    "/api/threads/{thread_id}/mcp/inputs/{request_id}/response",
    {
      params: { path: { thread_id: "root-one", request_id: "mcpinp-one" } },
      body: { action: "accept", content: { name: "Ada", choices: ["a"] } },
    },
  );
  await waitFor(() =>
    expect(screen.queryByLabelText("MCP input request")).toBeNull(),
  );
});

it("retains the exact answer after unknown delivery and retries only that response", async () => {
  client.POST.mockRejectedValueOnce(
    new Error("Lost acknowledgement"),
  ).mockResolvedValue({ data: { ...request, state: "accepted" } });
  mount();
  const input = (await screen.findByLabelText("Name")) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "Ada" } });
  fireEvent.click(screen.getByRole("button", { name: "Send response" }));
  await screen.findByText(/Delivery is uncertain/);
  expect(input.disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Retry same response" }));
  await waitFor(() => expect(client.POST).toHaveBeenCalledTimes(2));
  expect(client.POST.mock.calls[0]).toEqual(client.POST.mock.calls[1]);
});

it("allows editing a rejected form and does not mislabel validation as unknown delivery", async () => {
  client.POST.mockRejectedValue(
    new ApiError("Invalid form", 400, "mcp_input_invalid"),
  );
  mount();
  const input = (await screen.findByLabelText("Name")) as HTMLInputElement;
  fireEvent.change(input, { target: { value: "Ada" } });
  fireEvent.click(screen.getByRole("button", { name: "Send response" }));
  await screen.findByText("Invalid form");
  await waitFor(() => expect(input.disabled).toBe(false));
  expect(screen.queryByText(/Delivery is uncertain/)).toBeNull();
});
