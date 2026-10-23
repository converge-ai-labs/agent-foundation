import { cleanup, fireEvent, render, screen } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { initialConfig } from "../configuration";
import { AgentEditor } from "./editor";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../../auth/context", () => ({
  useClient: () => ({ http, workspace: () => http }),
}));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test", settings: {} },
    organization: { id: "org_test" },
    can: () => true,
    basePath: "/workspace/test",
  }),
}));
vi.mock("../toolsets", () => ({
  AgentToolsets: () => null,
  useToolsetCatalog: () => ({}),
}));
vi.mock("../choices", () => ({
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
  vi.resetAllMocks();
});

it("points a refused creation at the missing name, then the missing model", () => {
  http.GET.mockResolvedValue({
    data: { items: [], next_cursor: null },
    response: new Response(null, { status: 200 }),
  });
  const submit = vi.fn();
  const onUnnamed = vi.fn();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false, gcTime: 0 } },
  });
  const editor = (unnamed: boolean) => (
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <AgentEditor
          id="agent-form"
          initial={initialConfig()}
          pending={false}
          error={undefined}
          unnamed={unnamed}
          onUnnamed={onUnnamed}
          submit={submit}
        />
      </MemoryRouter>
    </QueryClientProvider>
  );
  const { rerender } = render(editor(true));
  const form = document.getElementById("agent-form")!;

  fireEvent.submit(form);
  expect(onUnnamed).toHaveBeenCalledOnce();
  expect(screen.getByText("Choose a model.")).toBeTruthy();

  rerender(editor(false));
  fireEvent.submit(form);
  expect(onUnnamed).toHaveBeenCalledOnce();
  expect(
    document.activeElement?.closest("section")?.querySelector("h2")
      ?.textContent,
  ).toBe("Model");
  expect(submit).not.toHaveBeenCalled();
});

it("keeps execution edits across disclosure toggles and opens invalid JSON on save", () => {
  http.GET.mockResolvedValue({
    data: { items: [], next_cursor: null },
    response: new Response(null, { status: 200 }),
  });
  const submit = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <MemoryRouter>
        <AgentEditor
          id="advanced-form"
          initial={{ ...initialConfig(), model: "test-model" }}
          pending={false}
          error={undefined}
          submit={submit}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  const execution = screen.getByRole("button", {
    name: "Advanced",
  });
  expect(execution.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(execution);
  fireEvent.change(screen.getByLabelText("Wait time (seconds)"), {
    target: { value: "12" },
  });
  fireEvent.click(execution);
  fireEvent.click(execution);
  expect(
    (screen.getByLabelText("Wait time (seconds)") as HTMLInputElement).value,
  ).toBe("12");
  const json = execution;
  fireEvent.change(screen.getByLabelText("Configuration JSON"), {
    target: { value: "invalid" },
  });
  fireEvent.click(json);
  fireEvent.submit(document.getElementById("advanced-form")!);
  expect(json.getAttribute("aria-expanded")).toBe("true");
  expect(submit).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Configuration JSON"), {
    target: { value: "{}" },
  });
  fireEvent.submit(document.getElementById("advanced-form")!);
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      execution: { tools: { foreground_wait_seconds: 12 } },
    }),
    undefined,
    null,
  );
});

it("rejects duplicate execution variable names before saving the agent", () => {
  http.GET.mockResolvedValue({
    data: { items: [], next_cursor: null },
    response: new Response(null, { status: 200 }),
  });
  const submit = vi.fn();
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <MemoryRouter>
        <AgentEditor
          id="variables-form"
          initial={{
            ...initialConfig(),
            model: "test-model",
            execution: { environment: { APP_MODE: "agent" } },
          }}
          pending={false}
          error={undefined}
          submit={submit}
        />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  fireEvent.click(screen.getByRole("button", { name: "Advanced" }));
  fireEvent.click(screen.getByRole("button", { name: "Add variable" }));
  const names = screen.getAllByLabelText("Variable name {{number}}");
  fireEvent.change(names[1], { target: { value: "APP_MODE" } });
  fireEvent.submit(document.getElementById("variables-form")!);
  expect(submit).not.toHaveBeenCalled();
  fireEvent.change(names[1], { target: { value: "OTHER" } });
  fireEvent.submit(document.getElementById("variables-form")!);
  expect(submit).toHaveBeenCalledWith(
    expect.objectContaining({
      execution: { environment: { APP_MODE: "agent", OTHER: "" } },
    }),
    undefined,
    null,
  );
});
