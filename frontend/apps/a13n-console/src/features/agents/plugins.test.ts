import { expect, test } from "vitest";
import {
  hasPluginForm,
  instanceNameError,
  suggestedInstanceName,
  validatePluginSettings,
} from "./plugins";

test("simple schemas use forms while recursive and schema-less configurations use JSON", () => {
  expect(hasPluginForm(null)).toBe(false);
  expect(
    hasPluginForm({
      type: "object",
      properties: { value: { type: "boolean" } },
    }),
  ).toBe(true);
  expect(
    hasPluginForm({ type: "object", properties: { child: { $ref: "#" } } }),
  ).toBe(false);
});

test("plugin schemas can reuse IDs without collisions and still reject invalid values", () => {
  const first = {
    $id: "urn:plugin:config",
    type: "object",
    properties: { count: { type: "integer" } },
  };
  const second = { ...first, properties: { count: { type: "string" } } };
  expect(() => validatePluginSettings(first, { count: 2 })).not.toThrow();
  expect(() => validatePluginSettings(second, { count: "two" })).not.toThrow();
  expect(() => validatePluginSettings(first, { count: "two" })).toThrow(
    /integer/,
  );
});

test("suggests distinct valid names and rejects invalid or duplicate instance names", () => {
  const plugins = [
    { plugin_key: "1.example", instance_name: "plugin-1.example", config: {} },
  ];
  expect(suggestedInstanceName("1.example", plugins)).toBe(
    "plugin-1.example-2",
  );
  expect(instanceNameError("plugin-1.example", plugins)).toBeDefined();
  expect(
    instanceNameError("plugin-1.example", plugins, "plugin-1.example"),
  ).toBeUndefined();
  for (const name of ["", "1name", "with space", "a".repeat(129)])
    expect(instanceNameError(name, [])).toBeDefined();
});
