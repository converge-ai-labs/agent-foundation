import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  act,
  cleanup,
  render,
  renderHook,
  screen,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { ProvidersPage } from "./page";
import { i18n } from "../../i18n";
import { useProviderTypes } from "./kind-providers";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  organizationWrite: true,
  hasWorkspace: true,
  manage: true,
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({
    http: { GET: state.GET },
    workspace: () => ({ GET: state.GET }),
  }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: state.hasWorkspace
      ? { id: "ws_test", name: "Research" }
      : undefined,
    organization: { id: "org_test", name: "Acme" },
    organizationCan: () => state.organizationWrite,
    can: () => state.manage,
  }),
}));
function Location() {
  return <output>{useLocation().search}</output>;
}
function mount(
  query = "category=web",
  kind: "workspace" | "organization" = "workspace",
) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <MemoryRouter
        initialEntries={[
          `/${kind === "workspace" ? "workspace/research" : "organization"}/settings?${query}`,
        ]}
      >
        <ProvidersPage />
        <Location />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
beforeEach(() => {
  state.GET.mockResolvedValue({ data: { items: [], next_cursor: null } });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.scrollIntoView = () => {};
});
afterEach(async () => {
  cleanup();
  await i18n.changeLanguage("en");
  vi.unstubAllGlobals();
  vi.resetAllMocks();
  state.organizationWrite = true;
  state.hasWorkspace = true;
  state.manage = true;
});
it("restores category, then switches domains without losing workspace context", async () => {
  const user = userEvent.setup();
  mount();
  await screen.findByText("No web providers yet");
  expect(state.GET).toHaveBeenCalledWith(
    "/api/v1/web-providers",
    expect.objectContaining({
      params: { query: expect.objectContaining({ limit: 100 }) },
    }),
  );
  await user.click(screen.getByRole("tab", { name: "Connector" }));
  await screen.findByText("No connector providers yet");
  expect(screen.getByRole("status").textContent).toContain(
    "category=connectors",
  );
  expect(screen.queryByRole("combobox", { name: "Scope" })).toBeNull();
});
it("shows workspace providers without offering mutations to a read-only member", async () => {
  state.organizationWrite = false;
  state.manage = false;
  mount();
  await screen.findByText("No web providers yet");
  expect(screen.queryByRole("button", { name: "Add provider" })).toBeNull();
});

it("localizes built-in browser type labels without refetching or rewriting cached definitions", async () => {
  const cache = new QueryClient();
  const catalog = {
    items: [
      { type: "docker", display_name: "Docker browser" },
      { type: "native", display_name: "Native browser (Linux debugging)" },
      { type: "custom", display_name: "Custom provider" },
    ],
  };
  cache.setQueryData(["browser-provider-types"], catalog);
  cache.setQueryData(["environment-provider-types"], catalog);
  const { result } = renderHook(
    () => ({
      browser: useProviderTypes("browser", false),
      environment: useProviderTypes("environment", false),
    }),
    {
      wrapper: ({ children }) => (
        <QueryClientProvider client={cache}>{children}</QueryClientProvider>
      ),
    },
  );
  await act(async () => {
    await i18n.changeLanguage("zh-CN");
  });
  expect(
    result.current.browser.data?.items.map((item) => item.display_name),
  ).toEqual(["Docker 浏览器", "本机浏览器（Linux 调试）", "Custom provider"]);
  expect(result.current.environment.data).toEqual(catalog);
  expect(cache.getQueryData(["browser-provider-types"])).toEqual(catalog);
  expect(state.GET).not.toHaveBeenCalled();
});
