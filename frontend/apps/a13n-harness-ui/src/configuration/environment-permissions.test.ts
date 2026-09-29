import { expect, it } from "vitest";
import schema from "../openapi.json";
import {
  fullControl,
  readOnly,
  permissionPreset,
} from "./environment-permissions";

it("full control authorizes every action, including computer use", () => {
  expect([...fullControl].sort()).toEqual(
    [...schema.components.schemas.EnvironmentAction.enum].sort(),
  );
});

it("read only permits file observation, not mutation, execution or computer use", () => {
  expect(readOnly).toEqual([
    "environment.file.stat",
    "environment.file.read_text",
    "environment.file.read_bytes",
    "environment.file.list",
    "environment.file.query",
    "environment.file.search_text",
    "environment.file.copy_source",
  ]);
});

it("recognizes exact presets regardless of action ordering without widening custom ceilings", () => {
  expect(permissionPreset({ operations: [...fullControl].reverse() })).toBe(
    "full",
  );
  expect(permissionPreset({ operations: [...readOnly].reverse() })).toBe(
    "read_only",
  );
  expect(permissionPreset(undefined)).toBeUndefined();
  expect(permissionPreset({})).toBeUndefined();
  expect(permissionPreset({ operations: [] })).toBeUndefined();
  expect(
    permissionPreset({ operations: ["environment.file.read_text"] }),
  ).toBeUndefined();
  expect(
    permissionPreset({
      operations: fullControl.filter(
        (action) => !action.startsWith("environment.computer."),
      ),
    }),
  ).toBeUndefined();
  expect(
    permissionPreset({
      operations: [...readOnly, "environment.computer.observe"],
    }),
  ).toBeUndefined();
});
