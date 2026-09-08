import { expect, test } from "vitest";
import { advancedConfig, buildConfig, initialConfig } from "./configuration";

test("ordinary editing preserves hidden configuration and leaves omission distinct from null", () => {
  const original = {
    ...initialConfig("Support"),
    model: { model_key: "support" },
    secret_requirements: [{ key: "support-token", required: true }],
  };
  const config = buildConfig(
    original,
    { model: original.model, instructions: "Updated instructions" },
    advancedConfig(original),
  );
  expect(config.secret_requirements).toEqual(original.secret_requirements);
  expect(config.instructions).toBe("Updated instructions");
  expect(config.output_spec).toBeUndefined();
  const advanced = JSON.parse(advancedConfig(original));
  advanced.output_spec = null;
  expect(
    buildConfig(original, { model: original.model }, JSON.stringify(advanced))
      .output_spec,
  ).toBeNull();
});

test("advanced fields cannot overwrite hidden or common fields", () => {
  const original = initialConfig("Support");
  expect(() =>
    buildConfig(original, { model: original.model }, '{"plugins": []}'),
  ).toThrow(/dedicated field/);
  expect(() =>
    buildConfig(
      original,
      { model: original.model },
      '{"instructions": "hidden override"}',
    ),
  ).toThrow(/dedicated field/);
});

test("schema validation reports the invalid advanced field", () => {
  const original = initialConfig("Support");
  const advanced = {
    ...JSON.parse(advancedConfig(original)),
    retries: { tools: -1 },
  };
  expect(() =>
    buildConfig(
      original,
      { model: { model_key: "support" } },
      JSON.stringify(advanced),
    ),
  ).toThrow(/retries\/tools/);
});
