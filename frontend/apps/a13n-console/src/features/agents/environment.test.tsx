import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { useState } from "react";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { initialConfig } from "./configuration";
import { AgentEnvironment } from "./environment";
import { AgentForm } from "./form";

const http = vi.hoisted(() => ({ GET: vi.fn(), PATCH: vi.fn() }));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    can: () => true,
    basePath: "/workspace/test",
  }),
}));
vi.mock("../search/selection", () => ({ AgentSearchSelection: () => null }));
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

it("saves the default environment independently while preserving the unsaved instruction draft", async () => {
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
  const response = (data: unknown) => ({
    data,
    response: new Response(null, { status: 200 }),
  });
  const initial = {
    ...initialConfig("Research"),
    model: { model_key: "research" },
    instructions: "Check the evidence.",
  };
  const agent = {
    id: "agent_test",
    default_environment_template_id: null,
  } as Schema["Agent"];
  const updated = { ...agent, default_environment_template_id: "env_test" };
  http.GET.mockResolvedValue(
    response({
      items: [{ id: "env_test", name: "Sandbox" }],
      next_cursor: null,
    }),
  );
  http.PATCH.mockResolvedValue(response(updated));
  const submit = vi.fn();
  function Editor() {
    const [resource, setResource] = useState({
      value: agent,
      etag: '"before"',
    });
    return (
      <AgentForm
        initial={initial}
        name="Research"
        version={7}
        pending={false}
        error={undefined}
        submit={submit}
        back="/agents"
        environment={
          <AgentEnvironment
            resource={resource}
            disabled={false}
            onSaved={async () => {
              setResource({ value: updated, etag: '"after"' });
            }}
          />
        }
      />
    );
  }
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <Editor />
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
  await waitFor(() => expect(http.PATCH).toHaveBeenCalledOnce());
  expect(http.PATCH.mock.calls[0][1]).toMatchObject({
    params: { header: { "If-Match": '"before"' } },
    body: { default_environment_template_id: "env_test" },
  });
  expect(Object.keys(http.PATCH.mock.calls[0][1].body)).toEqual([
    "default_environment_template_id",
  ]);
  await waitFor(() => expect(screen.queryByText("Saving…")).toBeNull());
  expect(
    screen.getByRole("textbox", { name: "System instructions" }),
  ).toHaveProperty("value", "Check the evidence. Keep the draft.");
  expect(submit).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      instructions: "Check the evidence. Keep the draft.",
    }),
    "Research",
    "",
    7,
  );
});
