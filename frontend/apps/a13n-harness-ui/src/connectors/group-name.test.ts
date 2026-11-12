import { expect, it } from "vitest";
import { defaultGroupName, groupNameError } from "./group-name";

it.each([
  ["Work GitHub", "github", "work-github"],
  [" My...GitHub__2 ", "github", "my-github-2"],
  ["私人账号", "github", "github"],
  ["123", "github", "connection-123"],
  ["a".repeat(31) + " z", "github", "a".repeat(31)],
])("suggests a canonical group from %s", (name, kind, expected) => {
  const group = defaultGroupName(name, kind, []);
  expect(group).toBe(expected);
  expect(groupNameError(group, [])).toBeUndefined();
});

it("resolves normalized and truncated collisions with readable numeric suffixes", () => {
  expect(defaultGroupName("GitHub", "github", ["github", "github-2"])).toBe(
    "github-3",
  );
  const base = "a".repeat(32);
  expect(defaultGroupName(base, "github", [base])).toBe("a".repeat(30) + "-2");
});

it.each([
  "",
  "GitHub",
  "a__b",
  "two words",
  "-github",
  "1github",
  "a--b",
  "a-",
  "a".repeat(33),
])("rejects noncanonical group %s", (value) =>
  expect(groupNameError(value, [])).toBeTruthy(),
);

it("validates explicit aliases and existing reservations", () => {
  expect(groupNameError("work-github-2", [])).toBeUndefined();
  expect(groupNameError("github", ["github"])).toBe(
    "Group name is already used by another connection.",
  );
});
