import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { expect, it, vi } from "vitest";
import { EnvironmentInstances } from "./instances";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
  useAccess: () => ({ workspace: { id: "ws_test" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../shared/page", () => ({
  PageActions: ({ children }: { children: React.ReactNode }) => children,
}));

it.each(["a13n.http-envd", "a13n.websocket-envd"])(
  "registers %s with a typed Device identity rather than raw state",
  async (type) => {
    http.GET.mockImplementation(async (path: string) => ({
      data: {
        items: path.endsWith("environment-providers")
          ? [{ id: "envp_device", name: "My connection", type, enabled: true }]
          : [],
      },
    }));
    http.POST.mockReset().mockResolvedValue({ data: { id: "env_device" } });
    const cache = new QueryClient({
      defaultOptions: { queries: { retry: false } },
    });
    render(
      <QueryClientProvider client={cache}>
        <MemoryRouter>
          <EnvironmentInstances />
        </MemoryRouter>
      </QueryClientProvider>,
    );
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "Create environment" }),
    );
    await user.click(screen.getByRole("combobox", { name: "Ownership" }));
    await user.click(screen.getByRole("option", { name: /^External target/ }));
    await user.click(screen.getByRole("combobox", { name: "Provider" }));
    await user.click(
      await screen.findByRole("option", { name: "My connection" }),
    );
    expect(
      screen.queryByRole("textbox", {
        name: "Connection configuration (JSON)",
      }),
    ).toBeNull();
    expect(screen.queryByText("Existing target state (optional)")).toBeNull();
    await user.type(
      screen.getByRole("textbox", { name: "Device ID" }),
      "my-laptop",
    );
    await user.click(
      screen.getByRole("button", { name: "Create environment" }),
    );
    await waitFor(() => expect(http.POST).toHaveBeenCalledOnce());
    expect(http.POST.mock.calls[0]).toEqual([
      "/api/v1/workspaces/{workspace}/environments",
      expect.objectContaining({
        body: {
          provider_id: "envp_device",
          configuration: {},
          configuration_schema_version: "1",
          device_id: "my-laptop",
        },
      }),
    ]);
    cache.clear();
  },
);
