import { cleanup, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
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
vi.mock("./choices", () => ({
  useAgentChoices: () => ({
    isPending: false,
    data: {
      models: ["original", "alternative"].map((key) => ({
        key,
        name: key,
        config: { model_name: key, model_api: "openai.chat_completions" },
      })),
      skills: [],
      mcp: [],
      connectors: [],
    },
  }),
}));
vi.mock("./toolsets", () => ({
  AgentToolsets: ({ readOnly }: { readOnly: boolean }) => (
    <button disabled={readOnly}>Change tools</button>
  ),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

it("keeps builtin configuration read-only, including the model", async () => {
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
    data: { items: [], next_cursor: null },
    response: new Response(null),
  });
  const initial = {
    model: "original",
    instructions: "Read evidence without configuration writes.",
    model_settings: { temperature: 0.2 },
    user_questions: false,
  };
  const submit = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({ defaultOptions: { queries: { retry: false } } })
      }
    >
      <MemoryRouter>
        <AgentEditor
          initial={initial}
          version={2}
          etag='"ap_finding:2"'
          pending={false}
          error={undefined}
          readonly
          submit={submit}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  expect(
    screen.queryByRole("textbox", { name: "System instructions" }),
  ).toBeNull();
  expect(
    screen.getByRole("group", { name: "System instructions" }).textContent,
  ).toContain(initial.instructions);
  expect(
    (screen.getByRole("button", { name: "Change tools" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    (screen.getByRole("combobox", { name: "Model" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    screen.queryByRole("spinbutton", { name: "Max output tokens" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  expect(submit).not.toHaveBeenCalled();
});
