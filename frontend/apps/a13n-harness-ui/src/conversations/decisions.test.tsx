// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import { ApiError, type Schema, type Transport } from "../transport/client";
import { DecisionForm } from "./decisions";

afterEach(cleanup);
const shell: Schema<"ApprovalRequestView"> = {
  kind: "approval",
  request_id: "shell-one",
  tool_name: "renamed_shell",
  arguments: JSON.stringify({
    command: "echo reviewed\nprintf done",
    cwd: "/workspace",
    alias: "workspace",
    environment: { TOKEN: "never-display-this" },
  }),
  metadata: {
    "a13n.harness.tool-approval": {
      tool_id: "environment.shell_exec",
      binding: "private-binding",
    },
    "a13n.harness.tool-review": {
      risk: "high",
      reason: "Writes to the workspace",
    },
    "a13n.harness.invocation-policy": {
      metadata: { token: "internal-policy" },
    },
  },
  override_allowed: false,
};
function setup(
  requests: Schema<"DecisionRequestView">[],
  POST = vi.fn().mockResolvedValue({ data: { receipt_id: "receipt-one" } }),
) {
  const reconcile = vi.fn();
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
      <TransportContext value={{ client: { POST } } as unknown as Transport}>
        <DecisionForm
          threadId="thread-one"
          batch={{ continuation_id: "C1", requests }}
          reconcile={reconcile}
        />
      </TransportContext>
    </QueryClientProvider>,
  );
  return { POST, reconcile };
}

it("shows shell evidence, hides environment values and authorizes only the exact original request", async () => {
  const { POST } = setup([shell]);
  expect(screen.getByText("Shell command approval")).toBeTruthy();
  expect(screen.getByText("Risk: high")).toBeTruthy();
  expect(screen.getByText("Writes to the workspace")).toBeTruthy();
  expect(screen.getByText("TOKEN (values hidden)")).toBeTruthy();
  expect(document.body.textContent).not.toMatch(
    /never-display-this|private-binding|internal-policy/,
  );
  expect(screen.queryByRole("button", { name: "Edit arguments" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
  fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
  await waitFor(() => expect(POST).toHaveBeenCalledOnce());
  expect(POST.mock.calls[0][1].body).toEqual({
    expected_continuation_id: "C1",
    responses: [{ kind: "approval", request_id: "shell-one", approved: true }],
  });
  await screen.findByText(/execution has not been confirmed/);
});

it("renders generic review context as text and denies with a reason", async () => {
  const { POST } = setup([
    {
      ...shell,
      tool_name: "publish",
      metadata: {
        "a13n.harness.approval-presentation": {
          target: "release/v1",
          reason: "<img src=x onerror=alert(1)>",
          risk: "medium",
        },
        note: "Ask the operator",
      },
      arguments: { release: "v1" },
    },
  ]);
  expect(screen.getByText("Tool approval")).toBeTruthy();
  expect(screen.getByText("Target: release/v1")).toBeTruthy();
  expect(screen.getByText("Risk: medium")).toBeTruthy();
  expect(screen.getByText("<img src=x onerror=alert(1)>")).toBeTruthy();
  expect(document.querySelector("img")).toBeNull();
  fireEvent.change(screen.getByRole("textbox", { name: "Reason (optional)" }), {
    target: { value: "Use staging first" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Deny" }));
  await waitFor(() => expect(POST).toHaveBeenCalledOnce());
  expect(POST.mock.calls[0][1].body.responses[0]).toEqual({
    kind: "approval",
    request_id: "shell-one",
    approved: false,
    denial_message: "Use staging first",
  });
});

it("blocks omitted arguments but keeps missing assessments answerable", async () => {
  const { POST } = setup([
    {
      ...shell,
      arguments: null,
      arguments_omitted: true,
      override_allowed: true,
    },
  ]);
  expect(
    screen.getByRole("button", { name: "Approve once" }).matches(":disabled"),
  ).toBe(true);
  expect(screen.queryByRole("button", { name: "Edit arguments" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
  expect(POST).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Deny" }));
  await waitFor(() => expect(POST).toHaveBeenCalledOnce());
  cleanup();
  setup([
    {
      ...shell,
      metadata: {
        "a13n.harness.tool-approval": { tool_id: "environment.shell_exec" },
        reason: "Review could not complete.",
      },
    },
  ]);
  expect(screen.getByText("Risk assessment unavailable")).toBeTruthy();
  expect(
    screen.getByRole("button", { name: "Approve once" }).matches(":disabled"),
  ).toBe(false);
});

it("submits question, generic approval and explicit external null together, not an empty result", async () => {
  const { POST } = setup([
    {
      kind: "question",
      request_id: "q",
      tool_name: "ask_user_question",
      questions: [
        {
          header: "Direction",
          question: "Where?",
          options: [{ label: "Left", description: "Go left" }],
          multi_select: false,
        },
      ],
    },
    {
      ...shell,
      request_id: "a",
      tool_name: "publish",
      metadata: {
        "a13n.harness.approval-presentation": { reason: "Operator approval" },
      },
      arguments: { target: "staging" },
    },
    {
      kind: "external",
      request_id: "e",
      tool_name: "external_lookup",
      arguments: { environment: { TOKEN: "external-secret" } },
    },
  ]);
  expect(document.body.textContent).not.toContain("external-secret");
  fireEvent.click(screen.getByRole("radio", { name: "Left Go left" }));
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Approval" }));
  await user.click(screen.getByRole("option", { name: "Approve once" }));
  await user.click(screen.getByRole("combobox", { name: "External result" }));
  await user.click(screen.getByRole("option", { name: "Provide a result" }));
  const submit = screen.getByRole("button", { name: "Submit responses" });
  expect(submit.matches(":disabled")).toBe(true);
  const editor = screen.getByRole("textbox", { name: "Result (JSON)" });
  fireEvent.change(editor, { target: { value: "invalid" } });
  expect(submit.matches(":disabled")).toBe(true);
  fireEvent.change(editor, { target: { value: "null" } });
  fireEvent.click(submit);
  await waitFor(() => expect(POST).toHaveBeenCalledOnce());
  expect(POST.mock.calls[0][1].body.responses).toEqual([
    { kind: "question", request_id: "q", answers: { "Where?": "Left" } },
    { kind: "approval", request_id: "a", approved: true },
    { kind: "external", request_id: "e", result: null },
  ]);
});

it.each([
  new TypeError("Connection lost"),
  new ApiError("Already resolved", 409),
])("never replays an uncertain or stale approval (%s)", async (error) => {
  const POST = vi.fn().mockRejectedValue(error);
  const { reconcile } = setup([shell], POST);
  fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
  await waitFor(() => expect(reconcile).toHaveBeenCalledOnce());
  fireEvent.click(screen.getByRole("button", { name: "Approve once" }));
  fireEvent.click(screen.getByRole("button", { name: "Refresh request" }));
  expect(POST).toHaveBeenCalledOnce();
});

it("allows a corrected singleton denial after an explicit validation rejection", async () => {
  const POST = vi
    .fn()
    .mockRejectedValueOnce(new ApiError("Invalid decision response", 422))
    .mockResolvedValueOnce({ data: { receipt_id: "corrected" } });
  const { reconcile } = setup([shell], POST);
  const reason = screen.getByRole("textbox", { name: "Reason (optional)" });
  fireEvent.change(reason, { target: { value: "x".repeat(32769) } });
  fireEvent.click(screen.getByRole("button", { name: "Deny" }));
  await waitFor(() => expect(reconcile).toHaveBeenCalledOnce());
  fireEvent.change(reason, { target: { value: "Use a read-only command." } });
  fireEvent.click(screen.getByRole("button", { name: "Deny" }));
  await waitFor(() => expect(POST).toHaveBeenCalledTimes(2));
  expect(POST.mock.calls[1][1].body.responses[0]).toMatchObject({
    approved: false,
    denial_message: "Use a read-only command.",
  });
});

it.each(["broken json", [1, 2], false, 0])(
  "retains generic non-object arguments: %s",
  (argumentsValue) => {
    setup([
      {
        kind: "approval",
        request_id: "a",
        tool_name: "custom",
        arguments: argumentsValue,
        override_allowed: false,
        metadata: { "a13n.harness.approval-presentation": "invalid" },
      },
    ]);
    expect(document.querySelector("pre")?.textContent).toBe(
      typeof argumentsValue === "string"
        ? argumentsValue
        : JSON.stringify(argumentsValue, null, 2),
    );
    expect(
      screen.getByRole("button", { name: "Approve once" }).matches(":disabled"),
    ).toBe(false);
  },
);
