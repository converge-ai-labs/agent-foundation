import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { FileTextIcon } from "@phosphor-icons/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../../service-client";
import { AssetAttachment } from "./attachment";
import { PreviewFileContext } from "../files/selection";
import { downloadBlob } from "../../../shared/download";

let client: Client;
let allowed = true;
vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "workspace" }, can: () => allowed }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("../../../shared/download", () => ({ downloadBlob: vi.fn() }));
afterEach(() => {
  cleanup();
  client.close();
  vi.clearAllMocks();
  allowed = true;
});

function mount(
  publication?: {
    runId: string;
    toolCallId: string;
    legacy: boolean;
  },
  onPreview?: (file: { assetId: string }) => void,
) {
  render(
    <QueryClientProvider
      client={
        new QueryClient({
          defaultOptions: {
            queries: { retry: false },
            mutations: { retry: false },
          },
        })
      }
    >
      <PreviewFileContext.Provider value={onPreview ?? null}>
        <AssetAttachment assetId="ast_fixture" publication={publication} />
      </PreviewFileContext.Provider>
    </QueryClientProvider>,
  );
}

it("loads file identity but downloads authenticated content only on request", async () => {
  const requests: Request[] = [];
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch: async (input, init) => {
      const request = new Request(input, init);
      requests.push(request);
      return request.url.endsWith("/content")
        ? new Response("# Local review", {
            headers: { "Content-Type": "text/markdown" },
          })
        : Response.json({
            id: "ast_fixture",
            name: "review.md",
            content_type: "text/markdown",
            size: 14,
            digest: "sha256:fixture",
            retired_at: null,
          });
    },
  });
  const onPreview = vi.fn();
  mount(undefined, onPreview);
  expect(await screen.findByText("review.md")).toBeTruthy();
  expect(requests).toHaveLength(1);
  const user = userEvent.setup();
  const preview = screen.getByRole("button", { name: "Preview {{name}}" });
  const { container } = render(<FileTextIcon size={13} aria-hidden="true" />);
  expect(preview.querySelector("svg")?.isEqualNode(container.firstChild)).toBe(
    true,
  );
  await user.click(preview);
  expect(onPreview).toHaveBeenCalledWith({
    assetId: "ast_fixture",
    publication: undefined,
  });
  expect(requests).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "Download review.md" }));
  await waitFor(() => expect(downloadBlob).toHaveBeenCalledOnce());
  expect(new URL(requests[1]!.url).pathname).toBe(
    "/api/v1/assets/ast_fixture/content",
  );
  expect(vi.mocked(downloadBlob).mock.calls[0]?.[1]).toBe("review.md");
  expect(
    requests.every(
      (request) => request.headers.get("X-Workspace-ID") === "workspace",
    ),
  ).toBe(true);
});

it("retains the asset reference without reading or downloading when permission is absent", () => {
  allowed = false;
  const fetch = vi.fn();
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch,
  });
  mount();
  expect(screen.getByText("ast_fixture")).toBeTruthy();
  expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
  expect(fetch).not.toHaveBeenCalled();
});

it.each([
  { source: { run_id: "other", tool_call_id: "call" } },
  { source: { run_id: "run", tool_call_id: "other" } },
  { source: null },
])(
  "does not offer a legacy attachment without matching source: $source",
  async ({ source }) => {
    const fetch = vi.fn(async () =>
      Response.json({ id: "ast_fixture", name: "other.md", source }),
    );
    client = createClient({
      baseUrl: "https://service.example",
      auth: { type: "session" },
      fetch,
    });
    mount({ runId: "run", toolCallId: "call", legacy: true });
    await waitFor(() => expect(fetch).toHaveBeenCalledOnce());
    expect(screen.queryByText("other.md")).toBeNull();
    expect(screen.queryByRole("button", { name: /Download/ })).toBeNull();
  },
);

it.each(["retired", "missing"])(
  "shows a %s publication without an enabled download",
  async (state) => {
    const fetch = vi.fn(async () =>
      state === "missing"
        ? Response.json({ error: { message: "Not found" } }, { status: 404 })
        : Response.json({
            id: "ast_fixture",
            name: "review.md",
            content_type: "text/markdown",
            retired_at: "2026-11-23T00:00:00Z",
            source: { run_id: "run", tool_call_id: "call" },
          }),
    );
    client = createClient({
      baseUrl: "https://service.example",
      auth: { type: "session" },
      fetch,
    });
    mount({ runId: "run", toolCallId: "call", legacy: false });
    expect(
      await screen.findByText(
        state === "retired" ? "File retired" : "File details unavailable",
      ),
    ).toBeTruthy();
    expect(
      (screen.getByRole("button", { name: /Download/ }) as HTMLButtonElement)
        .disabled,
    ).toBe(true);
    expect(fetch).toHaveBeenCalledOnce();
  },
);
