// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { McpInputs, useMcpInputRequests } from "./mcp-inputs";
import { i18n } from "../i18n";

function RequestForms() {
  const requests = useMcpInputRequests("root-one");
  return (
    <McpInputs
      threadId="root-one"
      requests={requests.data}
      reconcile={() => void requests.refetch()}
    />
  );
}
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
      <RequestForms />
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
  await act(() => i18n.changeLanguage("zh-CN"));
  expect((screen.getByLabelText("Name") as HTMLInputElement).value).toBe("Ada");
  expect((screen.getByLabelText("Alpha") as HTMLInputElement).checked).toBe(
    true,
  );
  expect(screen.getByText("Confirm this operation")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "接受" }));
  await act(() => i18n.changeLanguage("en"));
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
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  await screen.findByText(/Couldn't confirm delivery/);
  expect(input.disabled).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Retry response" }));
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
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  await screen.findByText("Invalid form");
  await waitFor(() => expect(input.disabled).toBe(false));
  expect(screen.queryByText(/Couldn't confirm delivery/)).toBeNull();
});

it.each([502, 503, 504])(
  "retains the exact answer after HTTP %i and retries only on request",
  async (status) => {
    client.POST.mockRejectedValueOnce(
      new ApiError("Gateway unavailable", status),
    ).mockResolvedValue({ data: { ...request, state: "accepted" } });
    mount();
    const input = (await screen.findByLabelText("Name")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Ada" } });
    fireEvent.click(screen.getByLabelText("Alpha"));
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await screen.findByText(/Couldn't confirm delivery/);
    expect(input.disabled).toBe(true);
    await waitFor(() => expect(client.GET).toHaveBeenCalledTimes(2));
    expect(client.POST).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Check request" }));
    await waitFor(() => expect(client.GET).toHaveBeenCalledTimes(3));
    expect(client.POST).toHaveBeenCalledTimes(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry response" }));
    await waitFor(() => expect(client.POST).toHaveBeenCalledTimes(2));
    expect(client.POST.mock.calls[0]).toEqual(client.POST.mock.calls[1]);
    expect(client.POST.mock.calls[1][1].body).toEqual({
      action: "accept",
      content: { name: "Ada", choices: ["a"] },
    });
  },
);

it.each(["mcp_input_conflict", "mcp_input_stale"])(
  "does not offer an uncertain-delivery retry for %s",
  async (code) => {
    client.POST.mockRejectedValue(
      new ApiError("Request unavailable", 409, code),
    );
    mount();
    const input = (await screen.findByLabelText("Name")) as HTMLInputElement;
    fireEvent.change(input, { target: { value: "Ada" } });
    fireEvent.click(screen.getByRole("button", { name: "Accept" }));
    await screen.findByText("Request unavailable");
    expect(input.disabled).toBe(true);
    expect(screen.queryByText(/Couldn't confirm delivery/)).toBeNull();
    expect(screen.queryByRole("button", { name: "Retry response" })).toBeNull();
    expect(client.POST).toHaveBeenCalledTimes(1);
  },
);

it("removes a gateway-failed request when reconciliation finds it accepted", async () => {
  client.POST.mockImplementation(async () => {
    client.GET.mockResolvedValue({ data: [{ ...request, state: "accepted" }] });
    throw new ApiError("Gateway unavailable", 504);
  });
  mount();
  fireEvent.change(await screen.findByLabelText("Name"), {
    target: { value: "Ada" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  await waitFor(() => expect(client.GET).toHaveBeenCalledTimes(2));
  await waitFor(() =>
    expect(screen.queryByLabelText("MCP input request")).toBeNull(),
  );
  expect(client.POST).toHaveBeenCalledTimes(1);
});

it("shows the URL destination and opens it only on explicit link activation", async () => {
  client.GET.mockResolvedValue({
    data: [
      {
        ...request,
        mode: "url",
        schema: null,
        url: "https://calendar.example.org/continue",
        url_host: "calendar.example.org",
        server_name: "Calendar integration",
      },
    ],
  });
  client.POST.mockResolvedValue({ data: { ...request, state: "accepted" } });
  mount();
  const link = await screen.findByRole("link", { name: "Open external page" });
  expect(screen.getByText("Calendar integration")).toBeTruthy();
  expect(screen.getByText("calendar.example.org")).toBeTruthy();
  expect(screen.queryByText(/Don.t include passwords/)).toBeNull();
  expect(link.getAttribute("href")).toBe(
    "https://calendar.example.org/continue",
  );
  expect(link.getAttribute("target")).toBe("_blank");
  expect(link.getAttribute("rel")).toBe("noopener noreferrer");
  expect(client.POST).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  await waitFor(() =>
    expect(client.POST).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ body: { action: "accept" } }),
    ),
  );
});

it("shows missing required input beside the field before submitting", async () => {
  mount();
  await screen.findByLabelText("Name");
  expect(screen.getByText(/Don.t include passwords/)).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Accept" }));
  expect(screen.getByText("This field is required.")).toBeTruthy();
  expect(screen.getByLabelText("Name").getAttribute("aria-invalid")).toBe(
    "true",
  );
  expect(client.POST).not.toHaveBeenCalled();
});

it("renders private OAuth callback forms as password fields without retaining submitted secrets", async () => {
  client.GET.mockResolvedValue({
    data: [
      {
        ...request,
        message: "",
        private_form: true,
        url: "https://accounts.google.com/o/oauth2/auth?state=fixture",
        url_host: "accounts.google.com",
        schema: {
          type: "object",
          required: ["callback"],
          properties: { callback: { type: "string", minLength: 1 } },
        },
      },
    ],
  });
  client.POST.mockResolvedValue({ data: { ...request, state: "accepted" } });
  mount();
  const link = await screen.findByRole("link", { name: "Open Google sign-in" });
  expect(link.getAttribute("href")).toContain("https://accounts.google.com/");
  const input = screen.getByLabelText(
    "Callback URL or authorization code",
  ) as HTMLInputElement;
  expect(input.type).toBe("password");
  fireEvent.change(input, { target: { value: "auth-code-123" } });
  fireEvent.click(screen.getByRole("button", { name: "Send response" }));
  await waitFor(() =>
    expect(client.POST).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({
        body: { action: "accept", content: { callback: "auth-code-123" } },
      }),
    ),
  );
});

it("renders ACP command permissions with structured evidence and direct option buttons", async () => {
  const permissionRequest = {
    ...request,
    thread_id: "root-one",
    source: "acp",
    server_id: "acp-antigravity",
    server_name: "Antigravity",
    mode: "permission",
    message: "",
    schema: null,
    permission: {
      tool_kind: "execute",
      tool_name: "run_command",
      command: "ps aux | grep -iE 'a13n|harness' | grep -v grep",
      cwd: "/home/wh1isper/code/oss/agent-foundation",
      environment_keys: ["API_KEY", "PATH"],
      locations: ["/home/wh1isper/code/oss/agent-foundation/Makefile:12"],
      arguments: { SafeToAutoRun: false },
      options: [
        { option_id: "reject_once", name: "Reject", kind: "reject_once" },
        { option_id: "allow_once", name: "Allow once", kind: "allow_once" },
        {
          option_id: "allow_always",
          name: "Allow always",
          kind: "allow_always",
        },
      ],
    },
  };
  client.GET.mockResolvedValue({ data: [permissionRequest] });
  client.POST.mockResolvedValue({
    data: { ...permissionRequest, state: "accepted" },
  });
  mount();
  expect(
    await screen.findByRole("heading", { name: "Command permission" }),
  ).toBeTruthy();
  expect(screen.getByText("Antigravity")).toBeTruthy();
  expect(screen.queryByText("acp-antigravity")).toBeNull();
  expect(screen.queryByText(/MCP server/)).toBeNull();
  expect(screen.queryByRole("combobox")).toBeNull();
  expect(document.querySelector("pre")?.textContent).toBe(
    "ps aux | grep -iE 'a13n|harness' | grep -v grep",
  );
  expect(
    screen.getByText("/home/wh1isper/code/oss/agent-foundation"),
  ).toBeTruthy();
  expect(
    screen.getByText("/home/wh1isper/code/oss/agent-foundation/Makefile:12"),
  ).toBeTruthy();
  expect(screen.getByText("API_KEY, PATH (values hidden)")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Copy command" })).toBeTruthy();
  const actionButtons = [
    screen.getByRole("button", { name: "Allow once" }),
    screen.getByRole("button", { name: "Allow always" }),
    screen.getByRole("button", { name: "Reject" }),
  ];
  expect(
    actionButtons[0].compareDocumentPosition(actionButtons[1]) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  expect(
    actionButtons[1].compareDocumentPosition(actionButtons[2]) &
      Node.DOCUMENT_POSITION_FOLLOWING,
  ).toBeTruthy();
  fireEvent.click(actionButtons[0]);
  await waitFor(() =>
    expect(client.POST).toHaveBeenCalledWith(
      "/api/threads/{thread_id}/mcp/inputs/{request_id}/response",
      {
        params: { path: { thread_id: "root-one", request_id: "mcpinp-one" } },
        body: { action: "accept", content: { option: "allow_once" } },
      },
    ),
  );
});

it("offers a Deny fallback when an ACP permission request has no reject option", async () => {
  const permissionRequest = {
    ...request,
    thread_id: "root-one",
    source: "acp",
    server_id: "acp-codex",
    server_name: "Codex",
    mode: "permission",
    message: "Write src/app.ts",
    schema: null,
    permission: {
      tool_kind: "edit",
      tool_name: "write_file",
      options: [
        { option_id: "allow_once", name: "Allow once", kind: "allow_once" },
      ],
    },
  };
  client.GET.mockResolvedValue({ data: [permissionRequest] });
  client.POST.mockResolvedValue({
    data: { ...permissionRequest, state: "declined" },
  });
  mount();
  expect(
    await screen.findByRole("heading", { name: "File edit permission" }),
  ).toBeTruthy();
  expect(screen.getByText("write_file")).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Deny" }));
  await waitFor(() =>
    expect(client.POST).toHaveBeenCalledWith(
      expect.any(String),
      expect.objectContaining({ body: { action: "decline" } }),
    ),
  );
});

it("omits the MCP server warning for ACP questions and renders enum form fields with one label and a placeholder", async () => {
  const questionRequest = {
    ...request,
    thread_id: "root-one",
    source: "acp",
    server_id: "acp-antigravity",
    server_name: "Antigravity",
    mode: "question",
    message: "",
    schema: null,
    questions: [
      {
        header: "Format",
        question: "Which output format?",
        multiSelect: false,
        options: [{ label: "JSON", description: "Machine-readable output" }],
      },
    ],
  };
  client.GET.mockResolvedValue({ data: [questionRequest] });
  mount();
  expect(await screen.findByRole("heading", { name: "Question" })).toBeTruthy();
  expect(screen.getByText("Antigravity")).toBeTruthy();
  expect(screen.queryByText(/MCP server/)).toBeNull();
  cleanup();

  const enumFormRequest = {
    ...request,
    thread_id: "root-one",
    source: "acp",
    server_id: "acp-antigravity",
    server_name: "Antigravity",
    mode: "form",
    message: "Select a deployment target",
    schema: {
      type: "object",
      properties: {
        target: {
          type: "string",
          title: "Environment",
          oneOf: [
            { const: "staging", title: "Staging" },
            { const: "prod", title: "Production" },
          ],
        },
      },
      required: ["target"],
    },
  };
  client.GET.mockResolvedValue({ data: [enumFormRequest] });
  mount();
  await screen.findByText("Select a deployment target");
  expect(screen.getAllByText("Environment")).toHaveLength(1);
  expect(screen.getByText("Select an option")).toBeTruthy();
  expect(
    screen.getByText(
      "Your response goes to this Agent. Don't include passwords, tokens or other secrets.",
    ),
  ).toBeTruthy();
  expect(screen.queryByText(/MCP server/)).toBeNull();
});
