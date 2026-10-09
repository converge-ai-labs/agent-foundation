import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ToastProvider } from "a13n-ui";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ApiError } from "../../service-client";
import { useAgentComposer } from "./composer";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
const access = vi.hoisted(() => ({ verbs: ["read", "run", "write"] }));
const models = vi.hoisted(() => ({ enabled: true, providerEnabled: true }));
vi.mock("../models/add-model", () => ({
  AddModel: ({
    onSaved,
    onClose,
  }: {
    onSaved: () => void;
    onClose: () => void;
  }) => (
    <div role="dialog" aria-label="Add model">
      <button
        onClick={() => {
          models.enabled = true;
          models.providerEnabled = true;
          onSaved();
        }}
      >
        Save model
      </button>
      <button onClick={onClose}>Cancel setup</button>
    </div>
  ),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    organization: { id: "org_test" },
    basePath: "/workspace/ws_test",
    can: (verb: string) => access.verbs.includes(verb),
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
  }),
}));
beforeEach(() => {
  access.verbs = ["read", "run", "write"];
  models.enabled = true;
  models.providerEnabled = true;
  http.GET.mockImplementation(async (path: string) => ({
    data: {
      items:
        path === "/api/v1/models"
          ? [{ key: "test", enabled: models.enabled, provider_id: "mp_test" }]
          : [{ id: "mp_test", enabled: models.providerEnabled }],
      next_cursor: null,
    },
  }));
});
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
                agent: { id: "ap_research", name: "Research" },
                revision: { id: "apr_three", number: 3 },
              })
            }
          >
            Configure from this version
          </button>
        </>
      )}
      {composer.setup}
      {composer.error && <p role="alert">{String(composer.error)}</p>}
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
  expect(http.POST).toHaveBeenCalledWith("/api/v1/agent-composer", {});
  expect(http.GET).toHaveBeenCalledWith("/api/v1/models", expect.anything());
});

it("opens setup before preparing the composer and resumes the original target after saving", async () => {
  models.enabled = false;
  http.POST.mockResolvedValue({ data: { id: "ap_composer" } });
  const user = renderLauncher();
  await user.click(
    screen.getByRole("button", { name: "Configure from this version" }),
  );
  await screen.findByRole("dialog", { name: "Add model" });
  expect(http.POST).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Current path").textContent).toBe("");
  await user.click(screen.getByRole("button", { name: "Save model" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalledOnce());
  await waitFor(() =>
    expect(screen.getByLabelText("Current path").textContent).toContain(
      "ap_composer",
    ),
  );
  const search = new URLSearchParams(
    screen.getByLabelText("Current path").textContent ?? "",
  );
  expect(search.get("message")).toContain("Research (ID ap_research)");
  expect(search.get("message")).toContain("revision ID apr_three");
  expect(screen.queryByRole("dialog")).toBeNull();
});

it("treats a disabled provider as unavailable and cancels without creating a composer", async () => {
  models.providerEnabled = false;
  const user = renderLauncher();
  await user.click(screen.getByRole("button", { name: "Create with AI" }));
  await screen.findByRole("dialog", { name: "Add model" });
  await user.click(screen.getByRole("button", { name: "Cancel setup" }));
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(http.POST).not.toHaveBeenCalled();
  expect(screen.getByLabelText("Current path").textContent).toBe("");
});

it("does not mistake a failed availability request for an empty workspace", async () => {
  http.GET.mockRejectedValue(new Error("Models unavailable"));
  const user = renderLauncher();
  await user.click(screen.getByRole("button", { name: "Create with AI" }));
  expect((await screen.findByRole("alert")).textContent).toContain(
    "Models unavailable",
  );
  expect(screen.queryByRole("dialog")).toBeNull();
  expect(http.POST).not.toHaveBeenCalled();
});

it("opens setup when the server detects a model that became unavailable", async () => {
  http.POST.mockRejectedValue(
    new ApiError(
      409,
      "conflict",
      "Model required",
      { reason: "model_required" },
      null,
    ),
  );
  const user = renderLauncher();
  await user.click(screen.getByRole("button", { name: "Create with AI" }));
  await screen.findByRole("dialog", { name: "Add model" });
  expect(screen.queryByRole("alert")).toBeNull();
});

it("lets runners converse with an existing composer without preparing it", async () => {
  access.verbs = ["read", "run"];
  http.GET.mockResolvedValue({
    data: {
      items: [{ id: "ap_composer", source: "builtin" }],
      next_cursor: null,
    },
  });
  const user = renderLauncher();
  await user.click(
    await screen.findByRole("button", { name: "Create with AI" }),
  );
  expect(screen.getByLabelText("Current path").textContent).toBe(
    "?agent=ap_composer",
  );
  expect(http.GET.mock.calls[0]?.[1].params.query.source).toBe("builtin");
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
    "Help me change the agent Research (ID ap_research), starting from its version 3 (revision ID apr_three).",
  );
});
