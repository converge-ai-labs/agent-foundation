import { cleanup, render, screen } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { initialConfig } from "./configuration";
import { AgentEditor } from "./editor";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => true,
    basePath: "/workspace/test",
  }),
}));
vi.mock("./toolsets", () => ({ AgentToolsets: () => null }));
vi.mock("../memory/availability", () => ({
  useMemoryProviders: () => ({ visible: false }),
  useWorkspaceMemoryProviderDefinitions: () => ({ data: { items: [] } }),
  eligibleMemoryProvider: () => false,
}));
vi.mock("../memory/presets", () => ({ MemoryPresets: () => null }));
vi.mock("../models/provider-definitions", () => ({
  useModelProviderDefinitions: () => ({ data: { items: [] } }),
}));
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
  http.GET.mockResolvedValue({
    data: {
      items: [{ id: "et_1234567890abcdef", name: "Sandbox" }],
      next_cursor: null,
    },
    response: new Response(null, { status: 200 }),
  });
  const submit = vi.fn();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <AgentEditor
          initial={{
            ...initialConfig("Research"),
            model: { model_key: "research" },
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
    screen.getByRole("combobox", { name: "Default environment" }),
  );
  await user.click(await screen.findByRole("option", { name: "Sandbox" }));
  expect(submit).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: /Save as v/ }));
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      instructions: "Check the evidence. Keep the draft.",
      default_environment_template_id: "et_1234567890abcdef",
    }),
    '"agent-v1"',
    null,
  );
});
