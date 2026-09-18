import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it, vi } from "vitest";
import { RunInspector } from "./inspector";

const mocks = vi.hoisted(() => ({ GET: vi.fn(), canRead: true }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: { GET: mocks.GET } }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/test",
    can: (action: string) => action === "environment.read" && mocks.canRead,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      options?.defaultValue ?? key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
vi.mock("./queries", () => ({
  useRun: () => ({
    data: {
      id: "run_test",
      status: "completed",
      agent_id: "agent_test",
      agent_revision_id: "ar_test",
      environment_id: "env_test",
      created_at: "2026-09-18T00:00:00Z",
      trigger_type: "user_input",
    },
  }),
}));
vi.mock("../agents/queries", () => ({
  useAgent: () => ({ data: { name: "Research agent" } }),
}));
vi.mock("./events", () => ({ RunEvents: () => null }));

it.each([true, false])(
  "shows readable Environment details only with read permission (%s)",
  async (allowed) => {
    mocks.canRead = allowed;
    mocks.GET.mockClear();
    mocks.GET.mockImplementation(async (path: string) => ({
      data: path.includes("/environments/")
        ? {
            id: "env_test",
            name: "Research workspace",
            ownership: "managed",
            provider_id: "envp_test",
            status: "stopped",
            access: "full",
            generation: 1,
            retention_condition: "idle",
            condition_since: "2026-09-18T00:00:00Z",
            updated_at: "2026-09-18T00:00:00Z",
            retention: { idle: { stop_after: null, delete_after: null } },
          }
        : path.includes("environment-providers")
          ? { id: "envp_test", name: "Local", type: "direct-local" }
          : { items: [] },
      response: new Response(null, { headers: { ETag: '"v1"' } }),
    }));
    const cache = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={cache}>
        <MemoryRouter>
          <RunInspector runId="run_test" onClose={() => {}} />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    if (allowed) {
      await screen.findByText("Research workspace");
      await userEvent
        .setup()
        .click(screen.getByRole("button", { name: "Details" }));
      const dialog = await screen.findByRole("dialog", {
        name: "Environment details",
      });
      expect(within(dialog).getByText("Research workspace")).toBeTruthy();
      expect(
        within(dialog).getByText("Effective retention policy"),
      ).toBeTruthy();
      expect(
        within(dialog).queryByRole("button", { name: "Stop target" }),
      ).toBeNull();
    } else {
      expect(screen.getByText("env_test")).toBeTruthy();
      expect(screen.queryByRole("button", { name: "Details" })).toBeNull();
      expect(
        mocks.GET.mock.calls.some(([path]) => path.includes("/environments/")),
      ).toBe(false);
    }
    cache.clear();
  },
);
