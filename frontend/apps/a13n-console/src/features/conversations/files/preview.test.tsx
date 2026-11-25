// @vitest-environment jsdom
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../../service-client";
import { downloadBlob } from "../../../shared/download";
import { FilePreview } from "./preview";
import type { Asset } from "./assets";
import { TEXT_LIMIT } from "./preview-data";

let client: Client;
vi.mock("../../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "workspace" } }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key, i18n: { language: "en" } }),
}));
vi.mock("../../../shared/download", () => ({ downloadBlob: vi.fn() }));
afterEach(() => {
  cleanup();
  client.close();
  vi.clearAllMocks();
  vi.unstubAllGlobals();
});

const asset: Asset = {
  id: "ast_fixture",
  name: "report.md",
  content_type: "text/markdown",
  size: 15,
  workspace_id: "workspace",
  digest: "fixture",
  source: null,
  retired_at: null,
  created_at: "",
  updated_at: "",
  created_by_id: "",
  version: 1,
};
function mount(fetch: typeof globalThis.fetch, initial = asset) {
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
    fetch,
  });
  const cache = new QueryClient();
  const tree = (value: Asset) => (
    <QueryClientProvider client={cache}>
      <FilePreview
        key={value.id}
        asset={value}
        onBack={vi.fn()}
        onClose={vi.fn()}
      />
    </QueryClientProvider>
  );
  const mounted = render(tree(initial));
  return {
    ...mounted,
    select: (value: Asset) => mounted.rerender(tree(value)),
  };
}

it("renders Markdown, toggles source, and keeps a separate download request", async () => {
  const fetch = vi.fn(async () => new Response("# Weekly review"));
  mount(fetch);
  expect(
    await screen.findByRole("heading", { name: "Weekly review" }),
  ).toBeTruthy();
  expect(downloadBlob).not.toHaveBeenCalled();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Source" }));
  expect(screen.getByRole("region", { name: "File source" }).textContent).toBe(
    "# Weekly review",
  );
  await user.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() =>
    expect(downloadBlob).toHaveBeenCalledWith(expect.anything(), "report.md"),
  );
  expect(fetch).toHaveBeenCalledTimes(2);
});

it("does not fetch large previews until Download is requested", async () => {
  const fetch = vi.fn(async () => new Response("# long report"));
  mount(fetch, { ...asset, size: TEXT_LIMIT + 1 });
  expect(
    screen.getByText(
      "This file is too large to preview. Download it to view the full file.",
    ),
  ).toBeTruthy();
  expect(fetch).not.toHaveBeenCalled();
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() => expect(downloadBlob).toHaveBeenCalledOnce());
});

it("aborts an old read and revokes its replacement's media URL on close", async () => {
  const createObjectURL = vi.fn(() => "blob:https://service.example/preview");
  const revokeObjectURL = vi.fn();
  class TestURL extends URL {
    static createObjectURL = createObjectURL;
    static revokeObjectURL = revokeObjectURL;
  }
  vi.stubGlobal("URL", TestURL);
  const seen: Request[] = [];
  let finishOld!: (response: Response) => void;
  const old = new Promise<Response>((resolve) => {
    finishOld = resolve;
  });
  const { select, unmount } = mount(async (input, init) => {
    const request = new Request(input, init);
    seen.push(request);
    return request.url.includes("ast_fixture")
      ? old
      : new Response("image bytes");
  });
  await waitFor(() => expect(seen).toHaveLength(1));
  select({
    ...asset,
    id: "ast_image",
    content_type: "image/png",
    name: "chart.png",
  });
  await screen.findByRole("img", { name: "chart.png" });
  expect(seen[0]?.signal.aborted).toBe(true);
  expect(createObjectURL).toHaveBeenCalledOnce();
  finishOld(new Response("# Stale report"));
  expect(screen.queryByRole("heading", { name: "Stale report" })).toBeNull();
  unmount();
  expect(revokeObjectURL).toHaveBeenCalledWith(
    "blob:https://service.example/preview",
  );
});
