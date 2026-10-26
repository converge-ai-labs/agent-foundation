import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { initialConfig } from "./configuration";
import { AgentEditor } from "./editor";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test", settings: {} },
    organization: { id: "org_test" },
    can: () => true,
    basePath: "/workspace/test",
  }),
}));
vi.mock("./toolsets", () => ({
  AgentToolsets: () => null,
  useToolsetCatalog: () => ({}),
}));
vi.mock("./choices", () => ({
  useAgentChoices: () => ({
    isPending: false,
    data: { models: [], providers: [], skills: [], mcp: [], connectors: [] },
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

it("preserves collapsed model settings and saves them with the environment choice", async () => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  HTMLElement.prototype.hasPointerCapture = () => false;
  HTMLElement.prototype.setPointerCapture = () => {};
  HTMLElement.prototype.releasePointerCapture = () => {};
  HTMLElement.prototype.scrollIntoView = () => {};
  let cloudName = "Sandbox";
  http.GET.mockImplementation(async (path: string) => ({
    data: {
      items:
        path === "/api/v1/environment-templates"
          ? [
              {
                id: "envtpl_local",
                name: "Sandbox",
                provider_id: "provider_local",
              },
              {
                id: "envtpl_1234567890abcdef1234",
                name: cloudName,
                provider_id: "provider_cloud",
              },
            ]
          : path === "/api/v1/environment-providers"
            ? [
                { id: "provider_local", type: "local" },
                { id: "provider_cloud", type: "e2b" },
              ]
            : [{ type: "e2b", display_name: "E2B" }],
      next_cursor: null,
    },
    response: new Response(null, { status: 200 }),
  }));
  const submit = vi.fn();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <AgentEditor
          initial={{
            ...initialConfig(),
            model: "model-0123456789abcdef0123",
            instructions: "Check the evidence.",
          }}
          version={7}
          etag='"agent-v1"'
          pending={false}
          error={undefined}
          submit={submit}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: /^Model settings/ }));
  await user.type(
    screen.getByRole("spinbutton", { name: "Max output tokens" }),
    "2048",
  );
  await user.click(screen.getByRole("button", { name: /^Model settings/ }));
  expect(submit).not.toHaveBeenCalled();
  expect(screen.getByText("{{value}} tokens")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: /^Model settings/ }));
  expect(
    (
      screen.getByRole("spinbutton", {
        name: "Max output tokens",
      }) as HTMLInputElement
    ).value,
  ).toBe("2048");
  await user.click(screen.getByRole("button", { name: /^Model settings/ }));
  await user.click(screen.getByRole("button", { name: /^Model settings/ }));
  await user.clear(
    screen.getByRole("textbox", { name: "Request parameters JSON" }),
  );
  await user.type(
    screen.getByRole("textbox", { name: "Request parameters JSON" }),
    '{{"temperature":0.4}',
  );
  await user.click(screen.getByRole("button", { name: /^Model settings/ }));
  await user.type(
    screen.getByRole("textbox", { name: "System instructions" }),
    " Keep the draft.",
  );
  await user.click(
    screen.getByRole("combobox", {
      name: "Environment",
    }),
  );
  const cloud = await screen.findByRole("group", { name: "Cloud hosting" });
  expect(
    within(screen.getByRole("group", { name: "Own hosting" })).getByRole(
      "option",
      { name: "Sandbox Local directory" },
    ),
  ).toBeTruthy();
  await user.click(within(cloud).getByRole("option", { name: "Sandbox E2B" }));
  expect(
    screen.getByRole("combobox", {
      name: "Environment",
    }).textContent,
  ).toContain("E2B");
  expect(submit).not.toHaveBeenCalled();
  cloudName = "Renamed in another tab";
  await user.click(
    within(screen.getByRole("region", { name: "Environment" })).getByRole(
      "button",
      { name: "Refresh" },
    ),
  );
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Environment" }).textContent,
    ).toContain(cloudName),
  );
  expect(submit).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      instructions: "Check the evidence. Keep the draft.",
      model_settings: { max_tokens: 2048, temperature: 0.4 },
      default_environment_template_id: "envtpl_1234567890abcdef1234",
    }),
    '"agent-v1"',
    null,
  );
});
