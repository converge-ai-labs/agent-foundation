import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "a13n-ui";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../service-client";
import { useAgentComposer } from "./composer";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
const access = vi.hoisted(() => ({ verbs: ["read", "run", "write"] }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test", key: "test" },
    organization: { id: "org_test" },
    basePath: "/workspace/test",
    can: (verb: string) => access.verbs.includes(verb),
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

function Launcher() {
  const composer = useAgentComposer();
  return (
    <>
      {composer.available && (
        <>
          <button type="button" onClick={() => composer.start()}>
            Create with AI
          </button>
          <button
            type="button"
            onClick={() =>
              composer.start({
                agent: { id: "ap_research", key: "research", name: "Research" },
                revision: { id: "apr_three", number: 3 },
              })
            }
          >
            Configure from this version
          </button>
        </>
      )}
      <output aria-label="Current path">{useLocation().search}</output>
    </>
  );
}

function renderLauncher() {
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
      <ToastProvider closeLabel="Dismiss">
        <MemoryRouter>
          <Launcher />
        </MemoryRouter>
      </ToastProvider>
    </QueryClientProvider>,
  );
  return userEvent.setup();
}

it("prepares the composer for writers and opens a conversation with it", async () => {
  access.verbs = ["read", "run", "write"];
  http.POST.mockResolvedValue({ data: { id: "ap_composer" } });
  const user = renderLauncher();
  await user.click(screen.getByRole("button", { name: "Create with AI" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Current path").textContent).toBe(
      "?agent=ap_composer",
    ),
  );
  expect(http.POST).toHaveBeenCalledWith(
    "/api/v1/workspaces/{workspace_id}/agent-composer",
    { params: { path: { workspace_id: "ws_test" } } },
  );
  expect(http.GET).not.toHaveBeenCalled();
});

it("explains the missing model instead of opening a conversation", async () => {
  access.verbs = ["read", "run", "write"];
  http.POST.mockRejectedValue(
    new ApiError(
      409,
      "conflict",
      "agent agent-composer: model required",
      { reason: "model_required" },
      null,
    ),
  );
  http.GET.mockResolvedValue({ data: { items: [], next_cursor: null } });
  const user = renderLauncher();
  await user.click(screen.getByRole("button", { name: "Create with AI" }));
  expect(
    await screen.findByText(
      "Configure a model provider to start Agent Composer.",
    ),
  ).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Open model setup" }));
  expect(screen.getByLabelText("Current path").textContent).toBe(
    "?category=models",
  );
});

it("lets runners converse with an existing composer without preparing it", async () => {
  access.verbs = ["read", "run"];
  http.GET.mockResolvedValue({
    data: { id: "ap_composer", source: "builtin" },
  });
  const user = renderLauncher();
  await user.click(
    await screen.findByRole("button", { name: "Create with AI" }),
  );
  expect(screen.getByLabelText("Current path").textContent).toBe(
    "?agent=ap_composer",
  );
  expect(http.GET.mock.calls[0]?.[1].params.path.agent_id).toBe(
    "agent-composer",
  );
  expect(http.POST).not.toHaveBeenCalled();
});

it("hides the entry point from runners while no composer exists", async () => {
  access.verbs = ["read", "run"];
  http.GET.mockRejectedValue(
    new ApiError(404, "not_found", "agent not found", {}, null),
  );
  renderLauncher();
  await waitFor(() => expect(http.GET).toHaveBeenCalledOnce());
  expect(screen.queryByRole("button", { name: "Create with AI" })).toBeNull();
});

it("opens the conversation with a first message naming the agent version to start from", async () => {
  access.verbs = ["read", "run", "write"];
  http.POST.mockResolvedValue({ data: { id: "ap_composer" } });
  const user = renderLauncher();
  await user.click(
    screen.getByRole("button", { name: "Configure from this version" }),
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Current path").textContent).not.toBe(""),
  );
  const search = new URLSearchParams(
    screen.getByLabelText("Current path").textContent ?? "",
  );
  expect(search.get("agent")).toBe("ap_composer");
  expect(search.get("message")).toBe(
    "Help me change the agent Research (key research, ID ap_research), starting from its version 3 (revision ID apr_three).",
  );
});
