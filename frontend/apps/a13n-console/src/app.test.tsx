import { act, cleanup, render, waitFor } from "@testing-library/react";
import { type ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { App } from "./app";
import { i18n } from "./i18n";

vi.mock("./auth/context", () => ({
  AuthProvider: ({ children }: { children: ReactNode }) => children,
  useAuth: () => ({ anonymous: false, error: null, isPending: false }),
  useClient: () => ({ http: {} }),
}));
vi.mock("./layout/shell", () => ({ Shell: () => null }));
vi.mock("./layout/workspace", () => ({
  WorkspaceProvider: ({ children }: { children: ReactNode }) => children,
  useWorkspace: () => ({ basePath: "/workspace/test" }),
}));

beforeEach(async () => {
  localStorage.clear();
  document.documentElement.lang = "";
  await i18n.changeLanguage("en");
});

afterEach(async () => {
  cleanup();
  localStorage.clear();
  await i18n.changeLanguage("en");
});

it("synchronizes the document language with the active translation", async () => {
  render(<App />);
  await waitFor(() => expect(document.documentElement.lang).toBe("en"));

  await act(async () => {
    await i18n.changeLanguage("zh-CN");
  });
  await waitFor(() => expect(document.documentElement.lang).toBe("zh-CN"));

  await act(async () => {
    await i18n.changeLanguage("en");
  });
  await waitFor(() => expect(document.documentElement.lang).toBe("en"));
});
