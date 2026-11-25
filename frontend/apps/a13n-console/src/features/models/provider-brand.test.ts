import { expect, it } from "vitest";
import { providerBrand } from "./model-icon";

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
