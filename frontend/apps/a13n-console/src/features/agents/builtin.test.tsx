import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { AgentEditor } from "./editor";
import type { Schema } from "../../shared/api";

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
      providers: [],
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
  useToolsetCatalog: () => ({}),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  vi.resetAllMocks();
});

it("keeps non-editable agents and reader views read-only, including the model", async () => {
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
  // The model reads as text, as the other fields do, not as a disabled picker.
  expect(screen.queryByRole("combobox", { name: "Model" })).toBeNull();
  expect(screen.getByRole("group", { name: "Model" }).textContent).toContain(
    initial.model,
  );
  expect(
    screen.queryByRole("spinbutton", { name: "Max output tokens" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Save changes" })).toBeNull();
  expect(submit).not.toHaveBeenCalled();
});

it("edits supplements through ordinary config and keeps the original ETag", async () => {
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
    instructions: "Old supplement.",
    model_settings: {},
    user_questions: false,
  };
  const preset: Schema["AgentRevision"] = {
    id: "apr_original",
    agent_id: "ap_finding",
    number: 2,
    config: initial,
    digest: "digest",
    note: null,
    created_at: "2026-10-22T00:00:00Z",
    created_by_id: "usr_test",
    builtin_instructions: "Current official policy.",
  };
  const submit = vi.fn();
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function editor(revision = preset, etag = '"ap_finding:2"') {
    return (
      <QueryClientProvider client={cache}>
        <MemoryRouter>
          <AgentEditor
            initial={revision.config}
            preset={revision}
            version={revision.number}
            etag={etag}
            pending={false}
            error={undefined}
            submit={submit}
          />
        </MemoryRouter>
      </QueryClientProvider>
    );
  }
  const { rerender } = render(editor());
  const user = userEvent.setup();
  const instructions = screen.getByRole("textbox", {
    name: "System instructions",
  });
  expect((instructions as HTMLTextAreaElement).value).toBe("Old supplement.");
  expect(
    (
      screen.getByRole("button", {
        name: "Change tools",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  expect(screen.getByRole("combobox", { name: "Model" })).toBeTruthy();
  await user.clear(instructions);
  await user.type(instructions, "New supplement.");
  await user.click(screen.getByRole("button", { name: /Model settings/ }));
  await user.type(
    screen.getByRole("spinbutton", { name: "Max output tokens" }),
    "2048",
  );
  // A background refresh cannot silently replace the draft or its precondition.
  rerender(
    editor(
      {
        ...preset,
        id: "apr_new",
        number: 3,
        config: { ...initial, instructions: "New official policy." },
      },
      '"ap_finding:3"',
    ),
  );
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(submit).toHaveBeenCalledWith(
      {
        ...initial,
        model_settings: { max_tokens: 2048 },
        instructions: "New supplement.",
      },
      '"ap_finding:2"',
      null,
    ),
  );
});
