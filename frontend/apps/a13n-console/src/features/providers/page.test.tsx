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
  POST: vi.fn(),
  organizationWrite: true,
  hasWorkspace: true,
  manage: true,
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({
    http: { GET: state.GET },
    workspace: () => ({ GET: state.GET, POST: state.POST }),
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

it.each([false, true])(
  "offers no manual Cloudflare setup (deployment availability: %s)",
  async (available) => {
    state.GET.mockImplementation(async (path) => ({
      data:
        path === "/api/v1/cloudflare-deployments"
          ? { available, items: [] }
          : {
              items:
                path === "/api/v1/provider-types/{kind}"
                  ? [
                      {
                        type: "cloudflare",
                        display_name: "Cloudflare Containers",
                      },
                      { type: "e2b", display_name: "E2B" },
                    ]
                  : [],
              next_cursor: null,
            },
    }));
    const user = userEvent.setup();
    mount("category=environments");
    await user.click(
      await screen.findByRole("button", { name: "Add provider" }),
    );
    await screen.findByRole("button", { name: "E2B" });
    const cloudflare = screen.queryByRole("button", {
      name: "Cloudflare Containers",
    });
    if (available) {
      await user.click(
        await screen.findByRole("button", { name: "Cloudflare Containers" }),
      );
      expect(
        screen.getByLabelText("Cloudflare API token").getAttribute("type"),
      ).toBe("password");
      expect(screen.queryByLabelText("Gateway URL")).toBeNull();
      expect(
        screen.queryByRole("button", { name: /existing gateway/i }),
      ).toBeNull();
    } else {
      expect(cloudflare).toBeNull();
    }
  },
);

it("localizes built-in browser and environment type labels without refetching or rewriting cached definitions", async () => {
  const cache = new QueryClient();
  const catalog = {
    items: [
      { type: "docker", display_name: "Docker browser" },
      { type: "native", display_name: "Native browser (Linux debugging)" },
      { type: "custom", display_name: "Custom provider" },
    ],
  };
  cache.setQueryData(["browser-provider-types"], catalog);
  const environmentCatalog = {
    items: [
      { type: "local", display_name: "Local directory (development only)" },
      { type: "custom", display_name: "Custom environment" },
    ],
  };
  cache.setQueryData(["environment-provider-types"], environmentCatalog);
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
  expect(
    result.current.browser.data?.items.map((item) => item.display_name),
  ).toEqual(["Docker browser", "Native browser", "Custom provider"]);
  await act(async () => {
    await i18n.changeLanguage("zh-CN");
  });
  expect(
    result.current.browser.data?.items.map((item) => item.display_name),
  ).toEqual(["Docker 浏览器", "本机浏览器", "Custom provider"]);
  expect(
    result.current.environment.data?.items.map((item) => item.display_name),
  ).toEqual(["本地目录（仅用于开发）", "Custom environment"]);
  expect(cache.getQueryData(["environment-provider-types"])).toEqual(
    environmentCatalog,
  );
  expect(cache.getQueryData(["browser-provider-types"])).toEqual(catalog);
  expect(state.GET).not.toHaveBeenCalled();
});

it("offers managed deployment with a separate write-only account token", async () => {
  state.GET.mockImplementation(async (path) => ({
    data:
      path === "/api/v1/cloudflare-deployments"
        ? { available: true, items: [] }
        : {
            items:
              path === "/api/v1/provider-types/{kind}"
                ? [
                    {
                      type: "cloudflare",
                      display_name: "Cloudflare Containers",
                      authentication: { mode: "required" },
                    },
                  ]
                : [],
            next_cursor: null,
          },
  }));
  state.POST.mockResolvedValue({ data: {} });
  const user = userEvent.setup();
  mount("category=environments");
  await user.click(await screen.findByRole("button", { name: "Add provider" }));
  await user.click(
    await screen.findByRole("button", { name: "Cloudflare Containers" }),
  );
  expect(screen.queryByLabelText("Gateway token")).toBeNull();
  await user.type(screen.getByLabelText("Name"), "Managed sandbox");
  await user.type(screen.getByLabelText("Account ID"), "A".repeat(32));
  await user.type(
    screen.getByLabelText("Cloudflare API token"),
    "write-only-fixture",
  );
  await user.click(screen.getByRole("button", { name: "Deploy provider" }));
  expect(state.POST).toHaveBeenCalledWith("/api/v1/cloudflare-deployments", {
    body: {
      name: "Managed sandbox",
      account_id: "a".repeat(32),
      api_token: "write-only-fixture",
      request_id: expect.stringMatching(/^[0-9a-f-]{36}$/),
    },
  });
});
