import { beforeEach, expect, it, vi } from "vitest";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { ApiError } from "../../service-client";
import { FileMemoryBrowser } from "./documents";
const http = vi.hoisted(() => ({
  GET: vi.fn(),
  POST: vi.fn(),
  PUT: vi.fn(),
  DELETE: vi.fn(),
}));
vi.mock("../../auth/context", () => ({ useClient: () => ({ http }) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const document = {
  id: "mdoc_test",
  kind: "semantic",
  title: "Runtime",
  description: "Project decision",
  text: "Use Python 3.13.",
  path: "semantic/runtime.md",
  version: 2,
  sources: [],
  created_at: "2026-09-18T00:00:00Z",
  saved_at: "2026-09-18T01:00:00Z",
};
const response = (data: unknown, etag = '"v2"') => ({
  data,
  response: new Response(null, { headers: { ETag: etag } }),
});
beforeEach(() => {
  vi.resetAllMocks();
  http.GET.mockImplementation(async (path: string, options) =>
    response(
      path.endsWith("memory-scopes")
        ? {
            items: [
              {
                id: "mstore_test",
                scope: "thread",
                subject_id: "thr_test",
                environment_id: "env_test",
              },
            ],
            next_cursor: null,
          }
        : path.endsWith("/documents")
          ? { items: [document], next_cursor: null }
          : path.endsWith("/revisions")
            ? [document, { ...document, version: 1 }]
            : path.endsWith("/organization")
              ? []
              : { ...document, version: options?.params?.query?.version ?? 2 },
    ),
  );
});
async function setup() {
  const user = userEvent.setup();
  const cache = new QueryClient({
    defaultOptions: {
      queries: { retry: false, gcTime: 0 },
      mutations: { retry: false },
    },
  });
  render(
    <QueryClientProvider client={cache}>
      <FileMemoryBrowser />
    </QueryClientProvider>,
  );
  await user.click(
    await screen.findByRole("combobox", { name: "Memory store" }),
  );
  await user.click(
    await screen.findByRole("option", { name: /thread · thr_test · env_test/ }),
  );
  await user.click(await screen.findByRole("button", { name: /Runtime/ }));
  await screen.findByRole("heading", { name: "Runtime" });
  return { user, cache };
}
it("sends the displayed version and ETag, preserves a conflicting draft and permits a safe retry", async () => {
  const { user } = await setup();
  http.PUT.mockRejectedValueOnce(
    new ApiError(412, "version_conflict", "The document changed.", {}, null),
  );
  http.PUT.mockResolvedValue(
    response({
      document: { ...document, version: 3 },
      change_id: "change_test",
    }),
  );
  await user.click(screen.getByRole("button", { name: "Edit" }));
  const editor = screen.getByRole("textbox", { name: "Markdown content" });
  await user.clear(editor);
  await user.type(editor, "Use Python 3.14.");
  expect(
    (screen.getByRole("button", { name: "New document" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("This resource changed");
  expect((editor as HTMLTextAreaElement).value).toBe("Use Python 3.14.");
  const first = http.PUT.mock.calls[0][1];
  expect(first.params.header["If-Match"]).toBe('"v2"');
  expect(first.body).toEqual({
    expected_version: 2,
    change: { type: "replace", text: "Use Python 3.14." },
  });
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(http.PUT).toHaveBeenCalledTimes(2));
  expect(http.PUT.mock.calls[1][1].params.header["Idempotency-Key"]).toBe(
    first.params.header["Idempotency-Key"],
  );
});
it("opens an exact historical version and disables editing until returning to the current head", async () => {
  const { user } = await setup();
  await user.click(screen.getByRole("button", { name: "Version history" }));
  await user.click(await screen.findByRole("button", { name: /^v1/ }));
  await waitFor(() =>
    expect(http.GET).toHaveBeenCalledWith(
      expect.stringContaining("{document_id}"),
      expect.objectContaining({
        params: expect.objectContaining({ query: { version: 1 } }),
      }),
    ),
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Edit",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await user.click(screen.getByRole("button", { name: "Current version" }));
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Edit",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
});
it("creates a document in the selected corpus with its chosen kind and path", async () => {
  const { user } = await setup();
  http.POST.mockResolvedValue(
    response({
      document: { ...document, id: "mdoc_new" },
      change_id: "change_new",
    }),
  );
  await user.click(screen.getByRole("button", { name: "New document" }));
  await user.type(
    screen.getByRole("textbox", { name: "Title" }),
    "Build steps",
  );
  await user.type(screen.getByRole("textbox", { name: "File name" }), "build");
  await user.type(
    screen.getByRole("textbox", { name: "Markdown content" }),
    "Run make test.",
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(http.POST).toHaveBeenCalled());
  expect(http.POST.mock.calls[0][1]).toMatchObject({
    params: { path: { scope_id: "mstore_test" } },
    body: {
      title: "Build steps",
      kind: "semantic",
      path: "semantic/build.md",
      text: "Run make test.",
    },
  });
});
