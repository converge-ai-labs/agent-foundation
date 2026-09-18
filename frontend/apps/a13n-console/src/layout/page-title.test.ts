import { expect, it } from "vitest";
import { pageName, sectionName } from "./page-title";

it("names the destination behind a workspace path", () => {
  expect(pageName("/workspace/design/agents")).toBe("Agents");
  expect(pageName("/workspace/design/agents/release-reviewer")).toBe("Agents");
  expect(pageName("/workspace/design/traces")).toBe("Traces");
  expect(pageName("/workspace/design/settings/members")).toBe("Settings");
});

it("names the settings scopes and leaves unknown paths unnamed", () => {
  expect(pageName("/settings/profile")).toBe("Personal settings");
  expect(pageName("/organization/settings")).toBe("Organization settings");
  expect(pageName("/")).toBeUndefined();
  expect(pageName("/workspace/design/nowhere")).toBeUndefined();
});

it("names the settings section a reader is on", () => {
  expect(sectionName("/workspace/design/settings/members")).toBe("Members");
  expect(sectionName("/settings/profile")).toBe("Profile");
  expect(sectionName("/organization/settings/workspaces")).toBe("Workspaces");
  expect(sectionName("/workspace/design/agents")).toBeUndefined();
  expect(sectionName("/workspace/design/settings/nowhere")).toBeUndefined();
});
