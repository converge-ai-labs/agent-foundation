import { expect, it } from "vitest";
import { modelBrand } from "../src/brand/model-brand";

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
  ["au.anthropic.claude-sonnet-5", "amazon-bedrock", "claude"],
  ["qwen3.6-flash", "alibaba", "qwen"],
  ["doubao-seed-2-1-pro-260628", "openai", "doubao"],
  ["bytedance-seed/seed-2-1-turbo", "openrouter", "doubao"],
  ["llama3.2", "ollama", "meta"],
  ["meta-llama/Llama-3.3-70B-Instruct", "together", "meta"],
  ["Claude Opus 4.6 (Thinking)", undefined, "claude"],
  ["Gemini 2.5 Pro (High)", undefined, "gemini"],
  ["GPT-OSS 120B (Medium)", undefined, "openai"],
  ["OpenAI: GPT-5.4", undefined, "openai"],
  ["DeepSeek R1", undefined, "deepseek"],
  ["Kimi K2.5", undefined, "kimi"],
  ["fictional-model", "typesafe", "typesafe"],
])("resolves %s with provider %s as %s", (upstream, provider, expected) => {
  expect(modelBrand(upstream, provider)).toBe(expected);
});
