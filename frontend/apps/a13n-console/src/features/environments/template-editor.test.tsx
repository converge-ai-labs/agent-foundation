import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  cleanup,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { TemplateEditor } from "./template-editor";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  PATCH: vi.fn(),
  POST: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(values?.[name] ?? name)),
    i18n: { resolvedLanguage: "en" },
  }),
}));
vi.mock("./template-config", () => ({
  TemplateConfig: () => <p>Template configuration editor</p>,
}));
afterEach(() => {
  cleanup();
  vi.clearAllMocks();
});

it("keeps a failed template list query out of the create dialog", async () => {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  await cache
    .fetchQuery({
      queryKey: ["environment-templates", "workspace", "ws_test", undefined],
      queryFn: () => Promise.reject(new Error("List unavailable")),
    })
    .catch(() => undefined);
  render(
    <QueryClientProvider client={cache}>
      <TemplateEditor
        scope={{ kind: "workspace", id: "ws_test" }}
        controlledOpen
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Template configuration editor")).toBeTruthy();
  expect(screen.queryByText("List unavailable")).toBeNull();
  cache.clear();
});

it("keeps settings drafts across tabs and saves against the original version", async () => {
  const template = {
    id: "et_test",
    name: "Original template",
    description: "Original description",
    default_revision_id: "etr_test",
    version: 1,
    archived_at: null,
  };
  state.GET.mockImplementation(async (path: string) => ({
    data: path.includes("template-revisions") ? {} : template,
    response: new Response(null, { headers: { ETag: '"version-1"' } }),
  }));
  state.PATCH.mockResolvedValue({ data: template });
  const close = vi.fn();
  const user = userEvent.setup();
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <TemplateEditor
        scope={{ kind: "workspace", id: "ws_test" }}
        templateId={template.id}
        controlledOpen
        onClose={close}
      />
    </QueryClientProvider>,
  );
  await user.click(await screen.findByRole("tab", { name: "Settings" }));
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "Save changes" })
      .disabled,
  ).toBe(true);
  await user.clear(screen.getByRole("textbox", { name: "Name" }));
  await user.type(
    screen.getByRole("textbox", { name: "Name" }),
    "Unsaved draft",
  );
  await user.click(screen.getByRole("tab", { name: "Configuration" }));
  await waitFor(() =>
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull(),
  );
  await user.click(screen.getByRole("tab", { name: "Settings" }));
  expect(
    screen.getByRole<HTMLInputElement>("textbox", { name: "Name" }).value,
  ).toBe("Unsaved draft");
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  await waitFor(() =>
    expect(state.PATCH).toHaveBeenCalledWith(
      "/api/v1/environment-templates/{template_id}",
      {
        params: {
          path: { template_id: template.id },
          header: { "If-Match": '"version-1"' },
        },
        body: {
          name: "Unsaved draft",
          description: template.description,
          archived: false,
        },
      },
    ),
  );
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
});

it("sets an earlier revision as the default with the template ETag", async () => {
  const template = {
    id: "et_test",
    name: "Sandbox",
    description: null,
    default_revision_id: "etr_v2",
    version: 2,
    archived_at: null,
  };
  const revisions = [
    { id: "etr_v2", version: 2, created_at: "2026-09-09T00:00:00Z" },
    { id: "etr_v1", version: 1, created_at: "2026-09-08T00:00:00Z" },
  ];
  state.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/revisions")
      ? { items: revisions, next_cursor: null }
      : path.includes("template-revisions")
        ? {}
        : { ...template },
    response: new Response(null, { headers: { ETag: '"version-2"' } }),
  }));
  state.POST.mockImplementation(async () => {
    template.default_revision_id = "etr_v1";
    return { data: { ...template } };
  });
  const user = userEvent.setup();
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: { queries: { retry: false, gcTime: 0 } },
        })
      }
    >
      <TemplateEditor
        scope={{ kind: "workspace", id: "ws_test" }}
        templateId={template.id}
        controlledOpen
        onClose={vi.fn()}
      />
    </QueryClientProvider>,
  );
  const row = (version: string) =>
    within(screen.getByText(version).closest("div")!);
  await user.click(await screen.findByRole("tab", { name: "Versions" }));
  await screen.findByText("Version 2");
  expect(row("Version 2").getByText("Default version")).toBeTruthy();
  await user.click(
    row("Version 1").getByRole("button", { name: "Set as default" }),
  );
  const confirm = await screen.findByRole("dialog", { name: "Set as default" });
  await user.click(
    within(confirm).getByRole("button", { name: "Set as default" }),
  );
  await waitFor(() =>
    expect(state.POST).toHaveBeenCalledWith(
      "/api/v1/environment-templates/{template_id}/revisions/{revision_id}/default",
      {
        params: {
          path: { template_id: template.id, revision_id: "etr_v1" },
          header: { "If-Match": '"version-2"' },
        },
      },
    ),
  );
  await user.click(await screen.findByRole("tab", { name: "Versions" }));
  await screen.findByText("Version 1");
  await waitFor(() =>
    expect(row("Version 1").getByText("Default version")).toBeTruthy(),
  );
  expect(
    row("Version 2").getByRole("button", { name: "Set as default" }),
  ).toBeTruthy();
});
