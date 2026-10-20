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
import { TooltipProvider } from "a13n-ui";
import { useState } from "react";
import { afterEach, expect, it, vi } from "vitest";
import { ApiError } from "../../service-client";
import type { AgentConfig } from "./configuration";
import { AgentToolsets } from "./toolsets";

const http = vi.hoisted(() => ({ GET: vi.fn(), POST: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/workspace/ws_test",
    organization: { id: "org_test" },
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
  description: "Web tools from the service catalogue.",
  default_enabled: false,
  config_schema: {},
  tools: ["search", "scrape", "fetch", "download"].map((key) => ({
    key,
    display_name: `Display ${key}`,
    description: `Catalogue description for ${key}`,
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

function renderDraft(
  starting: NonNullable<AgentConfig["toolsets"]>,
  onRequireReviewer = vi.fn(),
  onReview = vi.fn(),
) {
  function Draft() {
    const [value, setValue] = useState(starting);
    return (
      <>
        <AgentToolsets
          value={value}
          onChange={setValue}
          config={{
            model: "model-test",
            toolsets: value,
          }}
          agentId="ap_test"
          onRequireReviewer={onRequireReviewer}
          onReview={onReview}
        />
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
      {/* These assertions cover help content, not the hover delay. */}
      <TooltipProvider delay={0}>
        <Draft />
      </TooltipProvider>
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

it("renders catalogue labels and descriptions while keeping model-facing tool names", async () => {
  const group = {
    ...web,
    key: "catalog_extension",
    display_name: "Catalogue extension",
    tools: [
      { ...web.tools[0], resource_selector: null },
      { ...web.tools[1], resource_selector: null, description: "" },
    ],
  };
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? { items: [group] }
      : { items: [], next_cursor: null },
  }));
  const { user } = renderDraft({});
  const title = await screen.findByText("Catalogue extension");
  expect(
    screen.getByRole("checkbox", { name: "Enable Catalogue extension tools" }),
  ).toBeTruthy();
  expect(screen.getByText(group.description)).toBeTruthy();
  await user.click(title.closest("button")!);
  expect(screen.getByRole("checkbox", { name: "search" })).toBeTruthy();
  expect(screen.getByText("Catalogue description for search")).toBeTruthy();
  expect(screen.queryByText("Display search")).toBeNull();
  expect(screen.getByRole("checkbox", { name: "scrape" })).toBeTruthy();
  expect(screen.getByText("Display scrape")).toBeTruthy();
});

it("renders catalogue labels when descriptions are omitted", async () => {
  const { description: _description, ...group } = web;
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? {
          items: [
            {
              ...group,
              tools: group.tools.map(
                ({ description: _description, ...tool }) => tool,
              ),
            },
          ],
        }
      : { items: [], next_cursor: null },
  }));
  const { user } = renderDraft({});
  const title = await screen.findByText("Web");
  await user.click(title.closest("button")!);
  expect(screen.getByRole("checkbox", { name: "search" })).toBeTruthy();
  expect(screen.getByText("Display search")).toBeTruthy();
  expect(screen.queryByText(web.description)).toBeNull();
});

it("keeps disabled child settings, edits permissions, and validates the candidate without saving", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? { items: [web] }
      : { items: [], next_cursor: null },
  }));
  http.POST.mockResolvedValue({
    response: new Response(null, { status: 204 }),
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
      .getByRole("button", { name: "Allow" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(
    within(fetchPermissions).queryByRole("button", { name: /inherit/i }),
  ).toBeNull();
  expect(
    within(fetchPermissions).getByRole("button", { name: "Automatic review" }),
  ).toBeTruthy();
  expect(screen.getByText("This tool uses automatic review.")).toBeTruthy();
  await user.hover(
    within(fetchPermissions).getByRole("button", { name: "Allow" }),
  );
  expect(
    await screen.findByText("Run without asking for approval."),
  ).toBeTruthy();
  expect(draft().web.tools.search.config.max_results).toBe(7);
  expect(draft().web.tools.scrape.permission).toBe("review");
  const permissions = screen.getByRole("group", { name: "search permission" });
  expect(
    within(permissions)
      .getByRole("button", { name: "Ask" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  await user.click(within(permissions).getByRole("button", { name: "Allow" }));
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
      "/api/v1/agents/validate",
      expect.objectContaining({
        body: {
          config: {
            model: "model-test",
            toolsets: draft(),
          },
          agent_id: "ap_test",
        },
      }),
    ),
  );
  expect(screen.queryByRole("alert")).toBeNull();
});

it("shows why the Service would refuse the configuration, with its setup link", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? { items: [web] }
      : { items: [], next_cursor: null },
  }));
  const field = "toolsets.web.tools.search.config.provider_id";
  http.POST.mockRejectedValue(
    new ApiError(
      400,
      "invalid_argument",
      `${field}: web_provider wp_missing not found`,
      { field, reason: "web_provider wp_missing not found" },
      null,
    ),
  );
  renderDraft(initial);
  const alert = await screen.findByRole("alert", {}, { timeout: 2000 });
  expect(alert.textContent).toContain(
    `${field}: web_provider wp_missing not found`,
  );
  expect(
    within(alert)
      .getByRole("link", { name: "Manage Web Providers" })
      .getAttribute("href"),
  ).toBe("/workspace/ws_test/settings/providers?category=web");
  expect(http.POST).toHaveBeenCalledOnce();
});

it("defaults to the first compatible Web Provider and shows its logo in the selector", async () => {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? { items: [web] }
      : path.endsWith("/provider-types/{kind}")
        ? {
            items: [
              {
                type: "brave",
                operations: ["search"],
                authentication: { mode: "required" },
                configuration_schema: {},
              },
              {
                type: "exa",
                operations: ["search", "scrape"],
                authentication: { mode: "required" },
                configuration_schema: {},
              },
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
      : path.endsWith("/provider-types/{kind}")
        ? {
            items: [
              {
                type: "brave",
                operations: ["search"],
                authentication: { mode: "required" },
                configuration_schema: {},
              },
            ],
          }
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
      : path.endsWith("/provider-types/{kind}")
        ? {
            items: [
              {
                type: "brave",
                operations: ["search"],
                authentication: { mode: "required" },
                configuration_schema: {},
              },
            ],
          }
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
        : {
            items: [
              {
                type: "brave",
                operations: ["search"],
                authentication: { mode: "required" },
                configuration_schema: {},
              },
            ],
          },
    });
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

it("selects platform tools one by one, with writes asking by default", async () => {
  const tool = (key: string, display_name: string, permission: string) => ({
    ...web.tools[2],
    key,
    display_name,
    description: "",
    execution_id: `mcp/platform/${key}`,
    model_name: key,
    default_enabled: true,
    default_permission: permission,
  });
  const execution = {
    key: "execution",
    display_name: "Execution",
    description: "Sessions, threads, runs, traces and findings.",
    default_enabled: false,
    platform: true,
    config_schema: {},
    tools: [
      tool("get_trace", "GET /traces/{trace_id}", "allow"),
      tool("create_finding", "POST /findings", "ask"),
    ],
  };
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? { items: [web, execution] }
      : { items: [], next_cursor: null },
  }));
  http.POST.mockResolvedValue({
    response: new Response(null, { status: 204 }),
  });
  const { user, draft } = renderDraft({
    execution: {
      enabled: false,
      tools: {
        get_trace: { enabled: true, permission: "inherit" },
        create_finding: { enabled: true, permission: "inherit" },
      },
    },
  });
  // Platform groups stay out of the main list, under the advanced section.
  expect(await screen.findByText("Web")).toBeTruthy();
  expect(screen.queryByText("Execution")).toBeNull();
  await user.click(
    screen.getByRole("button", { name: "Advanced / Platform Features" }),
  );
  await user.click(
    screen.getByRole("checkbox", { name: "Enable Execution tools" }),
  );
  await user.click(screen.getByText("Execution").closest("button")!);
  await user.click(screen.getByRole("checkbox", { name: "get_trace" }));
  expect(draft().execution.enabled).toBe(true);
  expect(draft().execution.tools.get_trace.enabled).toBe(false);
  // An inherited permission shows the tool's default: a write asks.
  const writes = screen.getByRole("group", {
    name: "create_finding permission",
  });
  expect(
    within(writes)
      .getByRole("button", { name: "Ask" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  await user.click(within(writes).getByRole("button", { name: "Allow" }));
  expect(draft().execution.tools.create_finding).toEqual({
    enabled: true,
    permission: "allow",
  });
});

it("selects automatic review and opens a rule for the catalogue execution identity", async () => {
  const onRequireReviewer = vi.fn(),
    onReview = vi.fn();
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/toolsets")
      ? {
          items: [
            { ...web, tools: [{ ...web.tools[2], execution_id: "web.fetch" }] },
          ],
        }
      : { items: [], next_cursor: null },
  }));
  http.POST.mockResolvedValue({
    response: new Response(null, { status: 204 }),
  });
  const { user, draft } = renderDraft({}, onRequireReviewer, onReview);
  await user.click((await screen.findByText("Web")).closest("button")!);
  await user.click(screen.getByRole("button", { name: "Automatic review" }));
  expect(draft().web.tools.fetch.permission).toBe("review");
  expect(onRequireReviewer).toHaveBeenCalledOnce();
  await user.click(screen.getByRole("button", { name: "Tool review rule" }));
  expect(onReview).toHaveBeenCalledWith({
    selector: "web.fetch",
    label: "fetch",
  });
});
