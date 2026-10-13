import { cleanup, render, screen, within } from "@testing-library/react";
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
vi.mock("./toolsets", () => ({ AgentToolsets: () => null }));
vi.mock("./choices", () => ({
  useAgentChoices: () => ({
    isPending: false,
    data: { models: [], skills: [], mcp: [], connectors: [] },
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

it("saves the environment choice together with other configuration edits", async () => {
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
                name: "Sandbox",
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
  await user.type(
    screen.getByRole("textbox", { name: "System instructions" }),
    " Keep the draft.",
  );
  await user.click(
    screen.getByRole("combobox", { name: "Default environment template" }),
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
    screen.getByRole("combobox", { name: "Default environment template" })
      .textContent,
  ).toContain("E2B");
  expect(submit).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      instructions: "Check the evidence. Keep the draft.",
      default_environment_template_id: "envtpl_1234567890abcdef1234",
    }),
    '"agent-v1"',
    null,
  );
});
