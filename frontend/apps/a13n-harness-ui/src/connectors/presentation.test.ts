import { expect, it } from "vitest";
import { compactSetupAction, setupActionIcon } from "./presentation";
import type { Schema } from "../transport/client";

it("maps semantic hints independently of provider, action ID and label", () => {
  const action: Schema<"SetupAction"> = {
    id: "provider-specific-opaque-operation",
    label: "Check this account",
    kind: "submit",
    icon: "test",
  };
  expect(setupActionIcon(action)).toBeTruthy();
  expect(compactSetupAction(action, false)).toBe(true);
  expect(compactSetupAction(action, true)).toBe(false);
  expect(compactSetupAction({ ...action, kind: "confirm" }, false)).toBe(false);
  expect(setupActionIcon({ ...action, icon: "future-token" })).toBeUndefined();
  expect(compactSetupAction({ ...action, icon: "future-token" }, false)).toBe(
    false,
  );
  expect(
    setupActionIcon({ id: "test", label: "Test", kind: "submit" }),
  ).toBeUndefined();
  expect(
    setupActionIcon({ id: "opaque", label: "Guide", kind: "open_url" }),
  ).toBeTruthy();
  expect(
    setupActionIcon({ id: "opaque", label: "Code", kind: "copy" }),
  ).toBeTruthy();
});
