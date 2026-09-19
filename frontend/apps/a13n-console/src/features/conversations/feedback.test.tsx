import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { RunFeedback } from "./feedback";

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
      <RunFeedback
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
      <RunFeedback
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
      <RunFeedback
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

function renderQuestions(
  presentation: Schema["JsonValue"] = questionPresentation,
) {
  post.mockResolvedValue({
    data: { run_id: "next" },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
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
            call_id: "question",
            kind: "user_input",
            tool_name: "ask_user_question",
            provider_type: null,
            presentation,
          },
        ]}
      />
    </QueryClientProvider>,
  );
  return userEvent.setup();
}
const questionPresentation = {
  questions: [
    {
      header: "Business",
      question: "Which business?",
      options: [
        { label: "Retail", description: "Orders and returns" },
        { label: "Software", description: "Product support" },
      ],
    },
    {
      header: "Tools",
      question: "Which tools?",
      multiSelect: true,
      options: [
        { label: "Search", description: "Find answers" },
        { label: "Tickets", description: "Escalate issues" },
      ],
    },
  ],
};

it("renders questions and submits single and multiple selections in the exact answer envelope", async () => {
  const user = renderQuestions();
  expect(screen.getByText("Which business?")).toBeTruthy();
  expect(screen.getByText("Orders and returns")).toBeTruthy();
  const submit = screen.getByRole("button", {
    name: "Submit responses",
  }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  await user.click(screen.getByRole("radio", { name: /Retail/ }));
  expect(submit.disabled).toBe(true);
  await user.click(screen.getByRole("checkbox", { name: /Search/ }));
  await user.click(screen.getByRole("checkbox", { name: /Tickets/ }));
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body.resolutions).toEqual([
    {
      action: "respond",
      call_id: "question",
      response: {
        answers: {
          "Which business?": "Retail",
          "Which tools?": ["Search", "Tickets"],
        },
      },
    },
  ]);
});

it("allows free text instead of an option and does not submit an empty answer", async () => {
  const user = renderQuestions({
    questions: [questionPresentation.questions[0]!],
  });
  await user.click(
    screen.getByRole("radio", { name: "Write your own answer" }),
  );
  const submit = screen.getByRole("button", {
    name: "Submit responses",
  }) as HTMLButtonElement;
  expect(submit.disabled).toBe(true);
  await user.type(
    screen.getByRole("textbox", { name: "Business: Your answer" }),
    "Travel support",
  );
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body.resolutions[0].response).toEqual({
    answers: { "Which business?": "Travel support" },
  });
});

it("submits questions and approval decisions together without dropping either", async () => {
  const user = userEvent.setup();
  post.mockResolvedValue({
    data: { run_id: "next" },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <RunFeedback
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
            call_id: "question",
            kind: "user_input",
            tool_name: "ask_user_question",
            provider_type: null,
            presentation: { questions: [questionPresentation.questions[0]!] },
          },
          {
            call_id: "approval",
            kind: "approval",
            tool_name: "Search",
            provider_type: null,
            presentation: {
              risk: "low",
              reason: "Tool policy requires approval.",
            },
          },
        ]}
      />
    </QueryClientProvider>,
  );
  const submit = screen.getByRole("button", {
    name: "Submit responses",
  }) as HTMLButtonElement;
  await user.click(screen.getByRole("radio", { name: /Retail/ }));
  expect(submit.disabled).toBe(true);
  await user.click(screen.getByRole("button", { name: "Approve once" }));
  expect(submit.disabled).toBe(false);
  await user.click(submit);
  await waitFor(() => expect(post).toHaveBeenCalledTimes(1));
  expect(post.mock.calls[0]![1].body.resolutions).toEqual([
    {
      action: "respond",
      call_id: "question",
      response: { answers: { "Which business?": "Retail" } },
    },
    { action: "approve", call_id: "approval" },
  ]);
});
