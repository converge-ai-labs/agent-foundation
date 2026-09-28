import { expect, it } from "vitest";
import { defaultModelKey } from "./model-options";

it("derives the key the Service gives a model created without one", () => {
  expect(defaultModelKey("openai", "anthropic/Claude Opus_5.1")).toBe(
    "openai-anthropic-claude-opus-5.1",
  );
  expect(defaultModelKey(undefined, "gpt-5")).toBeUndefined();
  expect(defaultModelKey("openai", " ")).toBeUndefined();
});
