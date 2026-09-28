import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { afterEach, expect, it, vi } from "vitest";
import { TemplateEditor } from "./template-editor";

const state = vi.hoisted(() => ({
  GET: vi.fn(),
  PATCH: vi.fn(),
  POST: vi.fn(),
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ http: state, workspace: () => state }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" }, can: () => true }),
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
      <TemplateEditor controlledOpen onClose={vi.fn()} />
    </QueryClientProvider>,
  );
  expect(await screen.findByText("Template configuration editor")).toBeTruthy();
  expect(screen.queryByText("List unavailable")).toBeNull();
  cache.clear();
});

it("keeps settings drafts across tabs and saves against the original version", async () => {
  const template = {
    id: "et_test",

    key: "original",
    name: "Original template",
    description: "Original description",
    version: 1,
    enabled: true,
  };
  state.GET.mockImplementation(async () => ({
    data: template,
    response: new Response(null, { headers: { ETag: '"version-1"' } }),
  }));
  state.PATCH.mockResolvedValue({
    data: { ...template, version: 2, enabled: false },
    response: new Response(null, { headers: { ETag: '"version-2"' } }),
  });
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
      <TemplateEditor templateId={template.id} controlledOpen onClose={close} />
    </QueryClientProvider>,
  );
  await user.click(await screen.findByRole("tab", { name: "Settings" }));
  expect(
    screen.getByRole<HTMLButtonElement>("button", { name: "Save changes" })
      .disabled,
  ).toBe(true);
  await user.clear(screen.getByRole("textbox", { name: "Name" }));
  await user.paste("Unsaved draft");
  await user.click(screen.getByRole("tab", { name: "Configuration" }));
  await waitFor(() =>
    expect(screen.queryByRole("textbox", { name: "Name" })).toBeNull(),
  );
  await user.click(screen.getByRole("tab", { name: "Settings" }));
  expect(
    screen.getByRole<HTMLInputElement>("textbox", { name: "Name" }).value,
  ).toBe("Unsaved draft");
  await user.click(screen.getByRole("switch", { name: "Archived" }));
  await user.click(screen.getByRole("button", { name: "Save changes" }));
  const path = { template_id: template.id };
  await waitFor(() =>
    expect(state.PATCH).toHaveBeenCalledWith(
      "/api/v1/environment-templates/{template_id}",
      {
        params: { path },
        headers: { "If-Match": '"version-1"' },
        // Archiving is the same change as the rename: one PATCH disables it.
        body: {
          name: "Unsaved draft",
          description: template.description,
          enabled: false,
        },
      },
    ),
  );
  expect(state.PATCH).toHaveBeenCalledOnce();
  expect(state.POST).not.toHaveBeenCalled();
  await waitFor(() => expect(close).toHaveBeenCalledOnce());
});
