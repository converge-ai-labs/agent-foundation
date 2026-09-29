import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it, vi } from "vitest";
import { ConnectionsPage } from "./page";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));

it("keeps provider navigation available beside empty-state connection setup", async () => {
  http.GET.mockResolvedValue({
    data: { items: [], next_cursor: null },
    response: new Response(),
  });
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false } },
        })
      }
    >
      <MemoryRouter>
        <ConnectionsPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await screen.findByText("No connections yet");
  const create = screen.getByRole("button", { name: "New connection" });
  expect(create.closest("header")).toBeNull();
  const manage = screen.getByRole("link", { name: "Manage providers" });
  expect(manage.closest("header")).not.toBeNull();
  expect(manage.getAttribute("href")).toBe(
    "/workspace/ws_test/settings/providers?category=connectors",
  );
  await userEvent.setup().click(create);
  expect(
    await screen.findByRole("dialog", { name: "New connection" }),
  ).toBeTruthy();
  expect(
    await screen.findByRole("button", { name: /Custom Remote MCP/ }),
  ).toBeTruthy();
});
