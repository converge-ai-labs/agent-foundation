import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import "../../i18n";
import { ModelIcon, ModelKeyLabel } from "./model-icon";

const http = vi.hoisted(() => ({ GET: vi.fn() }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: "ws_test" } }),
}));
afterEach(cleanup);

const page = (items: unknown[]) => ({
  data: { items, next_cursor: null },
  response: new Response(null, { status: 200 }),
});
function renderLabel(modelKey: string) {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  return render(
    <QueryClientProvider client={cache}>
      <ModelKeyLabel modelKey={modelKey} />
    </QueryClientProvider>,
  );
}

it("uses the catalog identity for gateway aliases", () => {
  const markup = renderToStaticMarkup(
    createElement(ModelIcon, {
      upstream: "gateway-alias",
      catalogRef: { provider: "anthropic", model: "claude-opus-5" },
    }),
  );
  expect(markup).toContain("claude-color.svg");
});

it("uses the generic icon when nothing names a brand", () => {
  const markup = renderToStaticMarkup(
    createElement(ModelIcon, { upstream: "local-scripted", catalogRef: null }),
  );
  expect(markup).toContain("<svg");
  expect(markup).not.toContain("<img");
});

it("holds a model key's place until the workspace models load", async () => {
  let loadModels = () => {};
  http.GET.mockImplementation((path: string) =>
    path === "/api/v1/models"
      ? new Promise((resolve) => {
          loadModels = () =>
            resolve(
              page([
                {
                  key: "team-fast",
                  name: "Team fast",
                  provider_id: "prov_ollama",
                  catalog_ref: null,
                  config: { model_name: "custom-fast" },
                },
              ]),
            );
        })
      : Promise.resolve(
          page([
            {
              id: "prov_ollama",
              type: "ollama",
              config: { base_url: "http://127.0.0.1:11434" },
            },
          ]),
        ),
  );
  const { container } = renderLabel("team-fast");
  expect(screen.queryByText("team-fast")).toBeNull();
  const loading = screen.getByRole("status");
  expect(loading.getAttribute("aria-busy")).toBe("true");
  expect(loading.textContent).toBe("Loading…");
  loadModels();
  await screen.findByText("Team fast");
  // Nothing in the upstream ID names a family, so the provider's brand stands in.
  await waitFor(() =>
    expect(container.querySelector("img")?.getAttribute("src")).toContain(
      "ollama",
    ),
  );
  expect(screen.queryByRole("status")).toBeNull();
});
