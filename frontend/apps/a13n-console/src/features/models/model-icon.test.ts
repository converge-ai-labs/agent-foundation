import { expect, it } from "vitest";
import { createElement } from "react";
import { renderToStaticMarkup } from "react-dom/server";
import { ModelIcon, modelBrand } from "./model-icon";

it("uses the catalog identity for gateway aliases", () => {
  const markup = renderToStaticMarkup(
    createElement(ModelIcon, {
      upstream: "gateway-alias",
      catalogRef: { provider: "anthropic", model: "claude-opus-5" },
    }),
  );
  expect(markup).toContain("claude-color.svg");
});

it("uses the generic custom icon for models without a catalog reference", () => {
  const markup = renderToStaticMarkup(
    createElement(ModelIcon, {
      upstream: "gpt-5.6",
      provider: "openai",
      catalogRef: null,
    }),
  );
  expect(markup).toContain("<svg");
  expect(markup).not.toContain("<img");
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
])("resolves %s before falling back to %s", (upstream, provider, expected) => {
  expect(modelBrand(upstream, provider)).toBe(expected);
});
