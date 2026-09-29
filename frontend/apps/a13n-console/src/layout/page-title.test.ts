// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, renderHook } from "@testing-library/react";
import { createElement } from "react";
import { MemoryRouter } from "react-router";
import { pageName, sectionName, usePageTitle } from "./page-title";

vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
afterEach(cleanup);

it("names the destination behind a workspace path", () => {
  expect(pageName("/workspace/design/agents")).toBe("Agents");
  expect(pageName("/workspace/design/agents/release-reviewer")).toBe("Agents");
  expect(pageName("/workspace/design/traces")).toBe("Traces");
  expect(pageName("/workspace/design/memories/mem_1")).toBe("Memories");
  expect(pageName("/workspace/design/settings/members")).toBe("Settings");
});

it.each([
  ["/workspace/design/models", "Models"],
  ["/workspace/design/sessions/sess_1", "Sessions"],
  ["/workspace/design/settings/members", "Members"],
  ["/settings/profile", "Profile"],
  ["/organization/settings/workspaces", "Workspaces"],
])("uses only the page name for %s in the browser tab", (path, title) => {
  renderHook(() => usePageTitle("Product lab"), {
    wrapper: ({ children }) =>
      createElement(MemoryRouter, { initialEntries: [path] }, children),
  });
  expect(document.title).toBe(title);
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
