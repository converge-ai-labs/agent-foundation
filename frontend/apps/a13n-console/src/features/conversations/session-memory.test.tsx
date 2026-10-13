import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { beforeEach, expect, it, vi } from "vitest";
import { SessionMemory } from "./session-memory";
import { fixtureRun, fixtureThread } from "./transcript/fixture";
import { runRequest } from "./request";
import { isInteractive } from "./transcript/run-actions";

const mocks = vi.hoisted(() => ({
  GET: vi.fn(),
  PATCH: vi.fn(),
  writable: true,
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => mocks }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/w",
    can: () => mocks.writable,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));

beforeEach(() => {
  mocks.writable = true;
  mocks.PATCH.mockReset().mockResolvedValue({ data: {} });
  mocks.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/organizations")
      ? { items: [fixtureRun({ status: "completed" })], next_cursor: null }
      : path.endsWith("/models")
        ? {
            items: [{ key: "local", name: "Local model", enabled: true }],
            next_cursor: null,
          }
        : {
            id: "ses_1",
            version: 3,
            memory: { enabled: false, auto_organize: false, model: null },
            memory_id: "mem_1",
            memory_status: { pending: false },
          },
  }));
});
function show() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <SessionMemory sessionId="ses_1" />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  return cache;
}

it("keeps opt-ins off, requires an explicit model, and saves with the session version", async () => {
  const cache = show();
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Memory" }));
  const memory = await screen.findByRole("switch", { name: "Session memory" });
  expect(memory.getAttribute("aria-checked")).toBe("false");
  await user.click(memory);
  await user.click(
    screen.getByRole("switch", { name: "Automatically organize saved memory" }),
  );
  expect(
    screen.getByRole("button", { name: "Save" }).hasAttribute("disabled"),
  ).toBe(true);
  await user.click(
    screen.getByRole("combobox", { name: "Organization model" }),
  );
  await user.click(await screen.findByRole("option", { name: "Local model" }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(mocks.PATCH).toHaveBeenCalledWith(
      "/api/v1/sessions/{session_id}",
      expect.objectContaining({
        headers: { "If-Match": '"ses_1:3"' },
        body: {
          memory: { enabled: true, auto_organize: true, model: "local" },
        },
      }),
    ),
  );
  expect(
    screen
      .getByRole("link", { name: "Browse files and revisions" })
      .getAttribute("href"),
  ).toBe("/w/memories/mem_1");
  expect(
    screen
      .getByRole("link", { name: "View run and usage" })
      .getAttribute("href"),
  ).toContain("view=debug");
  cache.clear();
});

it("allows readers to inspect history without changing settings", async () => {
  mocks.writable = false;
  const cache = show();
  await userEvent.click(screen.getByRole("button", { name: "Memory" }));
  const memory = await screen.findByRole("switch", { name: "Session memory" });
  expect(memory.getAttribute("aria-disabled")).toBe("true");
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  cache.clear();
});

it("labels organizer input as maintenance and excludes conversation controls", () => {
  const thread = fixtureThread({ purpose: "memory_organization" });
  expect(runRequest(fixtureRun(), thread).kind).toBe("memory_organization");
  expect(isInteractive(thread)).toBe(false);
});
