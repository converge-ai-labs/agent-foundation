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
  runnable: true,
}));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => mocks }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_test" },
    basePath: "/w",
    can: (verb: string) => verb === "run" && mocks.runnable,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const spaces = [{ name: "team", space_id: "msp_team", access: "write" }];
beforeEach(() => {
  mocks.runnable = true;
  mocks.PATCH.mockReset().mockResolvedValue({ data: {} });
  mocks.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/memory-spaces")
      ? {
          items: [{ id: "msp_team", name: "Team knowledge", enabled: false }],
          next_cursor: null,
        }
      : { id: "ses_1", version: 3, memory: { enabled: false, spaces } },
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
it("saves a participation override with run permission and retains disabled Space selections", async () => {
  const cache = show(),
    user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Memory" }));
  const memory = await screen.findByRole("switch", {
    name: "Use Memory Spaces",
  });
  expect(memory.getAttribute("aria-checked")).toBe("false");
  expect(
    (await screen.findByRole("link", { name: "Team knowledge" })).getAttribute(
      "href",
    ),
  ).toBe("/w/memory-spaces/msp_team");
  await user.click(memory);
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(mocks.PATCH).toHaveBeenCalledWith(
      "/api/v1/sessions/{session_id}",
      expect.objectContaining({
        headers: { "If-Match": '"ses_1:3"' },
        body: { memory: { enabled: true } },
      }),
    ),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  cache.clear();
});
it("keeps untouched participation omitted when explicitly clearing Space selection", async () => {
  const cache = show(),
    user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Memory" }));
  await user.click(await screen.findByRole("button", { name: "Remove Space" }));
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(mocks.PATCH).toHaveBeenCalledWith(
      "/api/v1/sessions/{session_id}",
      expect.objectContaining({ body: { memory: { spaces: [] } } }),
    ),
  );
  cache.clear();
});
it("keeps the Agent's Space default unresolved when only participation changes before first acceptance", async () => {
  mocks.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("/memory-spaces")
      ? { items: [], next_cursor: null }
      : {
          id: "ses_1",
          version: 1,
          last_run_id: null,
          memory: { enabled: false, spaces: [] },
        },
  }));
  const cache = show(),
    user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Memory" }));
  await user.click(
    await screen.findByRole("switch", { name: "Use Memory Spaces" }),
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(mocks.PATCH).toHaveBeenCalledWith(
      "/api/v1/sessions/{session_id}",
      expect.objectContaining({ body: { memory: { enabled: true } } }),
    ),
  );
  cache.clear();
});
it("preserves the draft and displays a rejected save", async () => {
  mocks.PATCH.mockResolvedValue({
    error: { error: { code: "conflict", message: "Space changed" } },
    response: new Response(null, { status: 409 }),
  });
  const cache = show(),
    user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Memory" }));
  await user.click(
    await screen.findByRole("switch", { name: "Use Memory Spaces" }),
  );
  await user.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() => expect(mocks.PATCH).toHaveBeenCalled());
  expect(screen.getByRole("dialog")).toBeTruthy();
  expect(
    screen
      .getByRole("switch", { name: "Use Memory Spaces" })
      .getAttribute("aria-checked"),
  ).toBe("true");
  cache.clear();
});
it("lets readers inspect selections without changing them", async () => {
  mocks.runnable = false;
  const cache = show();
  await userEvent.click(screen.getByRole("button", { name: "Memory" }));
  const memory = await screen.findByRole("switch", {
    name: "Use Memory Spaces",
  });
  expect(memory.getAttribute("aria-disabled")).toBe("true");
  expect(screen.queryByRole("button", { name: "Add Space" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  cache.clear();
});
it("labels organizer input as maintenance and excludes conversation controls", () => {
  const thread = fixtureThread({ purpose: "memory_organization" });
  expect(runRequest(fixtureRun(), thread).kind).toBe("memory_organization");
  expect(isInteractive(thread)).toBe(false);
});
