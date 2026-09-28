// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { CreateWorkspace } from "./create-workspace";

const http = vi.hoisted(() => ({ POST: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});
function Location() {
  return <output aria-label="Current path">{useLocation().pathname}</output>;
}

it("creates a named workspace and opens its settings", async () => {
  const user = userEvent.setup();
  http.POST.mockResolvedValue({
    data: { id: "ws_new", name: "Product Design" },
    response: new Response(),
  });
  render(
    <QueryClientProvider client={new QueryClient()}>
      <MemoryRouter>
        <Location />
        <CreateWorkspace organizationId="org_acme" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await user.click(screen.getByRole("button", { name: "Create workspace" }));
  await user.type(
    screen.getByRole("textbox", { name: "Workspace name" }),
    "Product Design",
  );
  await user.click(
    screen.getAllByRole("button", { name: "Create workspace" }).at(-1)!,
  );
  await waitFor(() =>
    expect(screen.getByLabelText("Current path").textContent).toBe(
      "/workspace/ws_new/settings",
    ),
  );
  expect(http.POST).toHaveBeenCalledWith(
    "/api/v1/organizations/{organization_id}/workspaces",
    {
      params: { path: { organization_id: "org_acme" } },
      body: { name: "Product Design" },
    },
  );
});
