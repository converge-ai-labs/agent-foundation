import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { PendingFeedback } from "./pending";

const { post, navigate } = vi.hoisted(() => ({
  post: vi.fn(),
  navigate: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { POST: post } }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/acme/design",
    workspace: { id: "workspace" },
    can: () => true,
  }),
}));
vi.mock("react-router", () => ({ useNavigate: () => navigate }));
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
      <PendingFeedback
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
  await user.click(screen.getAllByRole("button", { name: "Approve" })[0]!);
  expect(submit.disabled).toBe(true);
  expect(post).not.toHaveBeenCalled();
  await user.click(screen.getAllByRole("button", { name: "Reject" })[1]!);
  expect(submit.disabled).toBe(false);
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body).toEqual({
    expected_thread_version: 7,
    sealed_state_digest_sha256: "digest",
    resolutions: [
      { action: "approve", call_id: "first" },
      { action: "reject", call_id: "second" },
    ],
  });
});
