import { expect, it } from "vitest";
import { modelBrand } from "./model-icon";

it.each([
  ["anthropic/claude-sonnet-4", "openrouter", "claude"],
  ["google/gemini-2.5-pro", "openai", "gemini"],
  ["Qwen/Qwen3.5-Plus", "openai", "qwen"],
  ["deepseek/deepseek-r1", "openrouter", "deepseek"],
  ["gpt-4.1", "azure_openai", "openai"],
  ["o3-mini", "openrouter", "openai"],
  ["local-scripted", "openai", "openai"],
  ["claudesomething", "openrouter", "openrouter"],
])("resolves %s before falling back to %s", (upstream, provider, expected) => {
  expect(modelBrand(upstream, provider)).toBe(expected);
});
