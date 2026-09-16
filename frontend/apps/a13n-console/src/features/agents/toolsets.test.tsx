// @vitest-environment jsdom
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import type { AgentConfig } from "./configuration";
import { AgentToolsets } from "./toolsets";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test", key: "default" },
    can: () => true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
  }),
}));
const web = {
  key: "web",
  display_name: "Web",
  default_enabled: false,
  config_schema: {},
  tools: ["search", "scrape", "fetch", "download"].map((key) => ({
    key,
    display_name: `Display ${key}`,
    execution_id: key,
    model_name: key,
    default_enabled: false,
    default_permission: "allow",
    supported_permissions: ["inherit", "allow", "ask", "deny", "review"],
    config_schema: {},
    resource_selector: ["search", "scrape"].includes(key)
      ? { kind: "web_provider", operation: key }
      : null,
  })),
};
const initial: NonNullable<AgentConfig["toolsets"]> = {
  web: {
    enabled: false,
    tools: {
      search: { enabled: false, permission: "ask", config: { max_results: 7 } },
      scrape: { enabled: false, permission: "review", config: {} },
    },
  },
};

function renderDraft(starting: NonNullable<AgentConfig["toolsets"]>) {
  function Draft() {
    const [value, setValue] = useState(starting);
    return (
      <>
        <AgentToolsets value={value} onChange={setValue} reviewer={null} />
        <output data-testid="draft">{JSON.stringify(value)}</output>
      </>
    );
  }
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <Draft />
    </QueryClientProvider>,
  );
  return {
    user: userEvent.setup(),
    draft: () => JSON.parse(screen.getByTestId("draft").textContent!),
  };
}

afterEach(() => {
  cleanup();
  vi.resetAllMocks();
});

it("keeps disabled child settings, edits permissions, and validates the candidate without saving", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? { items: [web] }
      : { items: [], next_cursor: null },
  }));
  http.POST.mockResolvedValue({
    data: { valid: true, errors: [], toolsets: initial },
  });
  const { user, draft } = renderDraft(initial);
  await screen.findByText("Off");
  expect(screen.getByRole("heading", { name: "Tools" })).toBeTruthy();
  await user.click(screen.getByText("Web").closest("button")!);
  expect(screen.getByText("search")).toBeTruthy();
  expect(screen.queryByText("Display search")).toBeNull();
  expect(
    screen
      .getByRole("checkbox", { name: "search" })
      .hasAttribute("data-disabled"),
  ).toBe(true);
  expect(
    screen
      .getByRole("checkbox", { name: "scrape" })
      .hasAttribute("data-disabled"),
  ).toBe(true);
  expect(
    screen
      .getByRole("checkbox", { name: "fetch" })
      .hasAttribute("data-disabled"),
  ).toBe(true);
  expect(
    document.getElementById(
      screen
        .getByRole("checkbox", { name: "search" })
        .getAttribute("aria-describedby")!,
    )?.textContent,
  ).toBe("Add an enabled, configured Web Provider to enable this tool.");
  await user.click(screen.getByRole("button", { name: "Configure search" }));
  expect(screen.getByText("Web Provider")).toBeTruthy();
  await user.hover(
    screen.getByRole("button", { name: "Include domains help" }),
  );
  expect(
    await screen.findByText(
      /Only listed domains and their subdomains are allowed/,
    ),
  ).toBeTruthy();
  const fetchPermissions = screen.getByRole("group", {
    name: "fetch permission",
  });
  expect(
    within(fetchPermissions)
      .getByRole("button", { name: "allow" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(
    within(fetchPermissions).queryByRole("button", { name: /inherit/i }),
  ).toBeNull();
  expect(
    within(fetchPermissions).queryByRole("button", { name: /review/i }),
  ).toBeNull();
  expect(
    screen.getByText(
      "Review is configured for this tool. Choose another permission to replace it.",
    ),
  ).toBeTruthy();
  await user.hover(
    within(fetchPermissions).getByRole("button", { name: "allow" }),
  );
  expect(
    await screen.findByText("Run without asking for approval."),
  ).toBeTruthy();
  expect(draft().web.tools.search.config.max_results).toBe(7);
  expect(draft().web.tools.scrape.permission).toBe("review");
  const permissions = screen.getByRole("group", { name: "search permission" });
  expect(
    within(permissions)
      .getByRole("button", { name: "ask" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  await user.click(within(permissions).getByRole("button", { name: "allow" }));
  expect(draft().web.tools.search.config.max_results).toBe(7);
  expect(draft().web.tools.search.permission).toBe("allow");
  await user.click(screen.getByRole("checkbox", { name: "Enable Web tools" }));
  expect(draft().web.tools.search.config.max_results).toBe(7);
  expect(
    ["fetch", "download"].every((tool) => draft().web.tools[tool].enabled),
  ).toBe(true);
  expect(draft().web.tools.search.enabled).toBe(false);
  expect(draft().web.tools.scrape.enabled).toBe(false);
  await user.click(screen.getByRole("checkbox", { name: "Enable Web tools" }));
  expect(
    Object.values(draft().web.tools).every(
      (tool) => !(tool as { enabled: boolean }).enabled,
    ),
  ).toBe(true);
  expect(draft().web.tools.search.config.max_results).toBe(7);
  await waitFor(() =>
    expect(http.POST).toHaveBeenCalledWith(
      "/api/v1/workspaces/{workspace}/toolsets/validate",
      expect.objectContaining({
        body: expect.objectContaining({ toolsets: draft() }),
      }),
    ),
  );
});

it("defaults to the first compatible Web Provider and shows its logo in the selector", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? { items: [web] }
      : path.endsWith("/web-provider-types")
        ? {
            items: [
              { type: "brave", operations: ["search"] },
              { type: "exa", operations: ["search", "scrape"] },
            ],
          }
        : path.endsWith("/web-providers")
          ? {
              items: [
                {
                  id: "wprov_brave",
                  name: "Brave account",
                  type: "brave",
                  workspace_id: "ws_test",
                  enabled: true,
                  credential_configured: true,
                },
                {
                  id: "wprov_exa",
                  name: "Exa account",
                  type: "exa",
                  workspace_id: "ws_test",
                  enabled: true,
                  credential_configured: true,
                },
              ],
              next_cursor: null,
            }
          : { items: [], next_cursor: null },
  }));
  http.POST.mockResolvedValue({
    data: { valid: true, errors: [], toolsets: initial },
  });
  const { user, draft } = renderDraft(initial);
  await user.click((await screen.findByText("Web")).closest("button")!);
  await user.click(screen.getByRole("button", { name: "Configure search" }));
  const provider = screen.getByRole("combobox", { name: "Web Provider" });
  await waitFor(() =>
    expect(provider.querySelector("img")?.getAttribute("src")).toContain(
      "brave-color.svg",
    ),
  );
  expect(draft().web.tools.search.config.provider_id).toBeUndefined();
  expect(provider.querySelector("img")?.getAttribute("src")).toContain(
    "brave-color.svg",
  );
  expect(
    screen
      .getByRole("checkbox", { name: "search" })
      .hasAttribute("data-disabled"),
  ).toBe(true);
  expect(
    screen
      .getByRole("checkbox", { name: "scrape" })
      .hasAttribute("data-disabled"),
  ).toBe(true);
  await user.click(provider);
  expect(screen.queryByRole("option", { name: "No provider" })).toBeNull();
  expect(
    (await screen.findByRole("option", { name: /Exa account/ }))
      .querySelector("img")
      ?.getAttribute("src"),
  ).toContain("exa-color.svg");
  await user.click(screen.getByRole("checkbox", { name: "Enable Web tools" }));
  expect(draft().web.tools.search.config.provider_id).toBe("wprov_brave");
  expect(draft().web.tools.scrape.config.provider_id).toBe("wprov_exa");
  expect(draft().web.tools.search.enabled).toBe(true);
  expect(draft().web.tools.scrape.enabled).toBe(true);
});

it("shows only effective child access when a group is off and bulk toggles its children", async () => {
  const assets = {
    key: "assets",
    display_name: "Assets",
    default_enabled: false,
    config_schema: {},
    tools: [
      {
        key: "publish",
        display_name: "Publish",
        execution_id: "publish",
        model_name: "publish_asset",
        default_enabled: true,
        default_permission: "allow",
        supported_permissions: ["allow", "ask", "deny"],
        config_schema: {},
        resource_selector: null,
      },
    ],
  };
  const starting: NonNullable<AgentConfig["toolsets"]> = {
    assets: {
      enabled: false,
      tools: { publish: { enabled: true, permission: "ask" } },
    },
  };
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? { items: [assets] }
      : { items: [], next_cursor: null },
  }));
  http.POST.mockResolvedValue({
    data: { valid: true, errors: [], toolsets: starting },
  });
  const { user, draft } = renderDraft(starting);
  await user.click((await screen.findByText("Assets")).closest("button")!);
  const group = screen.getByRole("checkbox", { name: "Enable Assets tools" });
  const child = screen.getByRole("checkbox", { name: "publish_asset" });
  expect(group.getAttribute("aria-checked")).toBe("false");
  expect(child.getAttribute("aria-checked")).toBe("false");
  expect(child.hasAttribute("data-disabled")).toBe(true);
  expect(draft().assets.tools.publish.enabled).toBe(true);
  await user.click(group);
  expect(child.getAttribute("aria-checked")).toBe("true");
  expect(child.hasAttribute("data-disabled")).toBe(false);
  await user.click(group);
  expect(child.getAttribute("aria-checked")).toBe("false");
  expect(draft().assets.tools.publish.enabled).toBe(false);
});

it("does not replace an unavailable saved Web Provider when enabling the group", async () => {
  const starting: NonNullable<AgentConfig["toolsets"]> = {
    web: {
      enabled: false,
      tools: {
        search: {
          enabled: true,
          permission: "allow",
          config: { provider_id: "wprov_removed" },
        },
      },
    },
  };
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? { items: [web] }
      : path.endsWith("/web-provider-types")
        ? { items: [{ type: "brave", operations: ["search"] }] }
        : {
            items: [
              {
                id: "wprov_brave",
                name: "Brave",
                type: "brave",
                workspace_id: "ws_test",
                enabled: true,
                credential_configured: true,
              },
            ],
            next_cursor: null,
          },
  }));
  http.POST.mockResolvedValue({
    data: { valid: true, errors: [], toolsets: starting },
  });
  const { user, draft } = renderDraft(starting);
  await user.click((await screen.findByText("Web")).closest("button")!);
  const group = screen.getByRole("checkbox", { name: "Enable Web tools" });
  await waitFor(() => expect(group.hasAttribute("data-disabled")).toBe(false));
  await user.click(group);
  const search = screen.getByRole("checkbox", { name: "search" });
  expect(search.getAttribute("aria-checked")).toBe("false");
  expect(search.hasAttribute("data-disabled")).toBe(true);
  expect(screen.getByText("2 of 4 on")).toBeTruthy();
  expect(draft().web.tools.search.config.provider_id).toBe("wprov_removed");
  expect(draft().web.tools.search.enabled).toBe(false);
  await user.click(screen.getByRole("button", { name: "Configure search" }));
  expect(
    screen.getByText(
      "This provider is unavailable or does not support the selected operation.",
    ),
  ).toBeTruthy();
  expect(draft().web.tools.search.config.provider_id).toBe("wprov_removed");
});

it("does not show an enabled provider-backed tool without a saved provider reference", async () => {
  const starting: NonNullable<AgentConfig["toolsets"]> = {
    web: {
      enabled: true,
      tools: { search: { enabled: true, config: {} } },
    },
  };
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? { items: [web] }
      : path.endsWith("/web-provider-types")
        ? { items: [{ type: "brave", operations: ["search"] }] }
        : {
            items: [
              {
                id: "wprov_brave",
                name: "Brave",
                type: "brave",
                workspace_id: "ws_test",
                enabled: true,
                credential_configured: true,
              },
            ],
            next_cursor: null,
          },
  }));
  http.POST.mockResolvedValue({
    data: { valid: false, errors: [], toolsets: starting },
  });
  const { user, draft } = renderDraft(starting);
  await user.click((await screen.findByText("Web")).closest("button")!);
  const search = screen.getByRole("checkbox", { name: "search" });
  await waitFor(() => expect(search.hasAttribute("data-disabled")).toBe(false));
  expect(search.getAttribute("aria-checked")).toBe("false");
  expect(screen.getByText("0 of 4 on")).toBeTruthy();
  await user.click(search);
  expect(search.getAttribute("aria-checked")).toBe("true");
  expect(draft().web.tools.search.config.provider_id).toBe("wprov_brave");
});

it("enables local Web tools while Provider discovery is still pending", async () => {
  let finishProviders!: (value: unknown) => void;
  const pendingProviders = new Promise((resolve) => {
    finishProviders = resolve;
  });
  http.GET.mockImplementation((path: string) => {
    if (path.endsWith("/web-providers")) return pendingProviders;
    return Promise.resolve({
      data: path.endsWith("/toolsets")
        ? { items: [web] }
        : { items: [{ type: "brave", operations: ["search"] }] },
    });
  });
  http.POST.mockResolvedValue({
    data: { valid: true, errors: [], toolsets: initial },
  });
  const { user, draft } = renderDraft(initial);
  await user.click((await screen.findByText("Web")).closest("button")!);
  const group = screen.getByRole("checkbox", { name: "Enable Web tools" });
  await user.click(group);
  expect(group.getAttribute("aria-checked")).toBe("true");
  expect(draft().web.tools.fetch.enabled).toBe(true);
  expect(draft().web.tools.download.enabled).toBe(true);
  expect(draft().web.tools.search.enabled).toBe(false);
  finishProviders({
    data: {
      items: [
        {
          id: "wprov_brave",
          name: "Brave",
          type: "brave",
          workspace_id: "ws_test",
          enabled: true,
          credential_configured: true,
        },
      ],
      next_cursor: null,
    },
  });
  const search = screen.getByRole("checkbox", { name: "search" });
  await waitFor(() => expect(search.hasAttribute("data-disabled")).toBe(false));
  await user.click(search);
  expect(draft().web.tools.search.config.provider_id).toBe("wprov_brave");
});
