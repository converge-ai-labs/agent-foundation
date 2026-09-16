import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { ConfigurationFeedback } from "./feedback";

const { post, accepted } = vi.hoisted(() => ({
  post: vi.fn(),
  accepted: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { POST: post } }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("requires an explicit decision for every approval before sending the complete response set", async () => {
  const user = userEvent.setup();
  post.mockResolvedValue({
    data: { session_id: "session", thread_id: "thread", run_id: "next" },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ConfigurationFeedback
        accepted={accepted}
        run={
          {
            id: "run",
            sealed_state_digest_sha256: "digest",
          } as Schema["RunResource"]
        }
        thread={{ version: 7 } as Schema["ThreadResource"]}
        actions={[
          {
            call_id: "first",
            kind: "approval",
            tool_name: "First action",
            provider_type: null,
            presentation: null,
          },
          {
            call_id: "second",
            kind: "approval",
            tool_name: "Second action",
            provider_type: null,
            presentation: null,
          },
        ]}
      />
    </QueryClientProvider>,
  );
  const submit = screen.getByRole("button", {
    name: "Submit responses",
  }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  await user.click(screen.getAllByRole("button", { name: "Approve once" })[0]!);
  expect(submit.disabled).toBe(true);
  expect(post).not.toHaveBeenCalled();
  await user.click(screen.getAllByRole("button", { name: /^Deny$/ })[1]!);
  expect(submit.disabled).toBe(false);
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  await waitFor(() =>
    expect(accepted).toHaveBeenCalledWith({
      session_id: "session",
      thread_id: "thread",
      run_id: "next",
    }),
  );
  expect(post.mock.calls[0]![1].body).toEqual({
    expected_thread_version: 7,
    sealed_state_digest_sha256: "digest",
    resolutions: [
      { action: "approve", call_id: "first" },
      { action: "reject", call_id: "second" },
    ],
  });
});

it("submits a bounded denial reason with the existing feedback request", async () => {
  const user = userEvent.setup();
  post.mockResolvedValue({
    data: { session_id: "session", thread_id: "thread", run_id: "next" },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ConfigurationFeedback
        run={
          {
            id: "run",
            sealed_state_digest_sha256: "digest",
          } as Schema["RunResource"]
        }
        thread={{ version: 7 } as Schema["ThreadResource"]}
        actions={[
          {
            call_id: "first",
            kind: "approval",
            tool_name: "First action",
            provider_type: null,
            presentation: {
              target: "path: /workspace/report",
              risk: "high",
              reason: "Tool reviewer requires approval.",
            },
          },
        ]}
        accepted={accepted}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText("path: /workspace/report")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Deny with reason" }));
  const reason = screen.getByRole("textbox", {
    name: "Denial reason (optional)",
  });
  await user.type(reason, "Sensitive destination");
  await user.click(screen.getByRole("button", { name: "Submit responses" }));
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body.resolutions).toEqual([
    { action: "reject", call_id: "first", reason: "Sensitive destination" },
  ]);
});

it("falls back to JSON for malformed approval presentation", () => {
  render(
    <QueryClientProvider client={new QueryClient()}>
      <ConfigurationFeedback
        run={
          {
            id: "run",
            sealed_state_digest_sha256: "digest",
          } as Schema["RunResource"]
        }
        thread={{ version: 7 } as Schema["ThreadResource"]}
        actions={[
          {
            call_id: "first",
            kind: "approval",
            tool_name: "First action",
            provider_type: null,
            presentation: {
              target: "path: /workspace",
              reason: { unexpected: true },
            },
          },
        ]}
        accepted={accepted}
      />
    </QueryClientProvider>,
  );
  expect(screen.getByText("Request details")).toBeTruthy();
  expect(screen.queryByText("Review reason")).toBeNull();
});
