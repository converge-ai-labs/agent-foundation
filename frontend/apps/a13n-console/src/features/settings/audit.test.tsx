// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen } from "@testing-library/react";
import { afterEach, expect, it, vi } from "vitest";
import { Audit } from "./audit";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("lists the reader's own security activity, account-wide events included", async () => {
  http.GET.mockResolvedValue({
    data: {
      items: [
        {
          id: "aud_password",
          action: "user.password_change",
          actor_id: "usr_ada",
          actor: {
            id: "usr_ada",
            kind: "user",
            name: "Ada",
            email: "ada@example.com",
            status: "active",
            image_url: null,
          },
          target_kind: "user",
          target_id: "usr_ada",
          organization_id: null,
          workspace_id: null,
          outcome: "success",
          details: {},
          occurred_at: "2026-09-01T00:00:00Z",
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
      <Audit scope={{ kind: "personal" }} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("user.password_change")).toBeTruthy();
  expect(screen.getByText("ada@example.com")).toBeTruthy();
  expect(http.GET).toHaveBeenCalledWith("/api/v1/users/me/audit-events", {
    params: { query: { cursor: undefined, limit: 30 } },
    signal: expect.any(AbortSignal),
  });
});
