import { cleanup, render, screen, waitFor } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { afterEach, expect, it, vi } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import {
  ModelIcon,
  ModelKeyLabel,
  modelBrand,
  providerBrand,
} from "./model-icon";

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

it("reads a custom model's family from its upstream ID", () => {
  const markup = renderToStaticMarkup(
    createElement(ModelIcon, {
      upstream: "llama3.2",
      provider: "ollama",
      catalogRef: null,
    }),
  );
  expect(markup).toContain("meta-color.svg");
});

it("uses the generic icon when nothing names a brand", () => {
  const markup = renderToStaticMarkup(
    createElement(ModelIcon, { upstream: "local-scripted", catalogRef: null }),
  );
  expect(markup).toContain("<svg");
  expect(markup).not.toContain("<img");
});

it.each([
  [{ type: "openai", config: {} }, "openai"],
  [
    { type: "openai", config: { base_url: "http://127.0.0.1:24706/v1" } },
    undefined,
  ],
  [
    { type: "ollama", config: { base_url: "http://127.0.0.1:11434" } },
    "ollama",
  ],
])("brands provider %j as %s", (provider, expected) => {
  expect(providerBrand(provider)).toBe(expected);
});

it.each([
  ["anthropic/claude-sonnet-4", "openrouter", "claude"],
  ["google/gemini-2.5-pro", "openai", "gemini"],
  ["Qwen/Qwen3.5-Plus", "openai", "qwen"],
  ["deepseek/deepseek-r1", "openrouter", "deepseek"],
  ["gpt-4.1", "azure_openai", "openai"],
  ["o3-mini", "openrouter", "openai"],
  ["local-scripted", "openai", "openai"],
  ["claudesomething", "openrouter", "openrouter"],
  ["deepseek-v4-flash-0731", "alibaba", "deepseek"],
  ["glm-5.2", "alibaba", "zhipu"],
  ["us.openai.gpt-5.6-luna", "amazon-bedrock", "openai"],
  ["global.anthropic.claude-opus-5", "amazon-bedrock", "claude"],
  ["anthropic.claude-opus-4-8", "amazon-bedrock", "claude"],
  ["au.anthropic.claude-opus-4-8", "amazon-bedrock", "claude"],
  ["au.anthropic.claude-sonnet-5", "amazon-bedrock", "claude"],
  ["eu.anthropic.claude-fable-5-1", "amazon-bedrock", "claude"],
  ["jp.anthropic.claude-opus-5", "amazon-bedrock", "claude"],
  ["qwen3.6-flash", "alibaba", "qwen"],
  ["doubao-seed-2-1-pro-260628", "openai", "doubao"],
  ["bytedance-seed/seed-2-1-turbo", "openrouter", "doubao"],
  ["llama3.2", "ollama", "meta"],
  ["meta-llama/Llama-3.3-70B-Instruct", "together", "meta"],
  ["fictional-model", "typesafe", "typesafe"],
])("resolves %s before falling back to %s", (upstream, provider, expected) => {
  expect(modelBrand(upstream, provider)).toBe(expected);
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
  expect(container.querySelector('[data-slot="skeleton"]')).not.toBeNull();
  loadModels();
  await screen.findByText("Team fast");
  // Nothing in the upstream ID names a family, so the provider's brand stands in.
  await waitFor(() =>
    expect(container.querySelector("img")?.getAttribute("src")).toContain(
      "ollama",
    ),
  );
  expect(container.querySelector('[data-slot="skeleton"]')).toBeNull();
});
