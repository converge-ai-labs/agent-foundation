import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, Route, Routes } from "react-router";
import { useState, type ReactNode } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { initialConfig } from "./configuration";
import { AgentDetail } from "./detail";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
vi.mock("./settings", () => ({
  AgentActions: () => null,
  AgentDetails: () => null,
}));
vi.mock("./export", () => ({ ExportAgent: () => null }));
vi.mock("./editor", () => ({
  AgentEditor: ({
    initial,
    version,
    rail,
  }: {
    initial: { instructions: string };
    version: number;
    rail?: (summary: Record<string, unknown>) => ReactNode;
  }) => {
    const [opening] = useState({ instructions: initial.instructions, version });
    return (
      <div>
        <output aria-label="Editor version">v{opening.version}</output>
        <output aria-label="Editor instructions">{opening.instructions}</output>
        {rail?.({
          environmentId: null,
          skillCount: 0,
          connectionCount: 0,
          dirty: false,
        })}
      </div>
    );
  },
}));

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("reloads the selected configuration and version after Set as default", async () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.scrollIntoView = () => {};
  const agent = {
    id: "agt_1234567890abcdef",
    key: "research",
    name: "Research",
    description: null,
    image_url: null,
    enabled: true,
    archived_at: null,
    updated_at: "2026-09-17T00:00:00Z",
    default_revision_id: "agtr_v2",
  };
  const revisions = [
    {
      id: "agtr_v2",
      version: 2,
      config: { ...initialConfig("Research"), instructions: "Second" },
      created_at: "2026-09-17T00:00:00Z",
      created_by: { principal_id: "user" },
      change_summary: "Second",
    },
    {
      id: "agtr_v1",
      version: 1,
      config: { ...initialConfig("Research"), instructions: "First" },
      created_at: "2026-09-16T00:00:00Z",
      created_by: { principal_id: "user" },
      change_summary: null,
    },
  ];
  http.GET.mockImplementation(
    async (
      path: string,
      options?: { params?: { path?: { agent_revision_id?: string } } },
    ) => {
      if (path.endsWith("/revisions"))
        return { data: { items: revisions, next_cursor: null } };
      if (path.includes("/agent-revisions/"))
        return {
          data: revisions.find(
            (revision) =>
              revision.id === options?.params?.path?.agent_revision_id,
          ),
        };
      return {
        data: { ...agent },
        response: new Response(null, {
          headers: { ETag: `"${agent.default_revision_id}"` },
        }),
      };
    },
  );
  http.POST.mockImplementation(async () => {
    agent.default_revision_id = "agtr_v1";
    return { data: { agent: { ...agent }, revision: revisions[1] } };
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter initialEntries={["/workspace/test/agents/research"]}>
        <Routes>
          <Route
            path="/workspace/test/agents/:agentKey"
            element={<AgentDetail />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect((await screen.findByLabelText("Editor version")).textContent).toBe(
    "v2",
  );
  expect(screen.getByLabelText("Editor instructions").textContent).toBe(
    "Second",
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("tab", { name: /Versions/ }));
  await user.click(
    await screen.findByRole("button", { name: "Set as default" }),
  );
  const confirm = await screen.findByRole("dialog", { name: "Set as default" });
  await user.click(
    within(confirm).getByRole("button", { name: "Set as default" }),
  );
  await user.click(screen.getByRole("tab", { name: "Configuration" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Editor version").textContent).toBe("v1"),
  );
  expect(screen.getByLabelText("Editor instructions").textContent).toBe(
    "First",
  );
  expect(http.POST.mock.calls[0]?.[1].params.header["If-Match"]).toBe(
    '"agtr_v2"',
  );
  expect(http.POST.mock.calls[0]?.[1].body).toEqual({});
});
