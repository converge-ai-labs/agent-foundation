import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../../service-client";
import { AssetAttachment } from "./attachment";
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

function mount() {
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
      <AssetAttachment assetId="ast_fixture" />
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
  mount();
  expect(await screen.findByText("review.md")).toBeTruthy();
  expect(requests).toHaveLength(1);
  const user = userEvent.setup();
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
