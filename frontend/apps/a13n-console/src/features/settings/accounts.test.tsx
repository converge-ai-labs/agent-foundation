// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, within } from "@testing-library/react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { ServiceAccounts } from "./accounts";
import { ApiKeys } from "./keys";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_design" },
    basePath: "/workspace/design",
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: { defaultValue?: string }) =>
      options?.defaultValue ?? key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("describes each service account in its row", async () => {
  http.GET.mockResolvedValue({
    data: {
      items: [
        {
          id: "sa_deploy",
          name: "Deploy bot",
          description: "Publishes releases from CI.",
          role: "runner",
          status: "active",
          organization_id: "org_acme",
          workspace_id: "ws_design",
          version: 1,
          created_at: "2026-09-01T00:00:00Z",
          updated_at: "2026-09-01T00:00:00Z",
        },
      ],
      next_cursor: null,
    },
    response: new Response(),
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <ServiceAccounts />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Deploy bot")).toBeTruthy();
  expect(screen.getByText("Publishes releases from CI.")).toBeTruthy();
});

it("offers key creation only in the empty state for a service account", async () => {
  http.GET.mockResolvedValue({
    data: { items: [], next_cursor: null },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <ApiKeys accountId="sa_deploy" actions="inline" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const empty = (await screen.findByText("No API keys")).closest<HTMLElement>(
    '[data-slot="empty"]',
  )!;
  expect(screen.getAllByRole("button", { name: "Create key" })).toEqual([
    within(empty).getByRole("button", { name: "Create key" }),
  ]);
});
