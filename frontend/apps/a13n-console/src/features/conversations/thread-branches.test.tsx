import { cleanup, render, screen, within } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import { BranchOrigin, ThreadBranches } from "./thread-branches";
import {
  fixtureChildThread,
  fixtureForkThread,
  fixtureThread,
} from "./transcript/fixture";

vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ basePath: "/workspace/ws_1" }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, options?: Record<string, unknown>) =>
      key.replace(/{{(\w+)}}/g, (_, name) => String(options?.[name] ?? "")),
  }),
}));
afterEach(cleanup);
const nested = fixtureThread({
  id: "thr_nested",
  origin: "fork",
  origin_thread_id: fixtureForkThread.id,
  origin_run_id: "run_fork",
  created_at: "2026-09-21T00:00:00Z",
  last_run_id: "run_nested",
});
const threads = [
  nested,
  fixtureChildThread,
  fixtureForkThread,
  fixtureThread(),
];

it.each(["chat", "debug"] as const)(
  "switches branches with stable names and preserves %s",
  async (level) => {
    const { rerender } = render(
      <MemoryRouter>
        <ThreadBranches thread={nested} threads={threads} level={level} />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("button", { name: "Switch conversation" }).textContent,
    ).toBe("Branch 2");
    const user = userEvent.setup();
    await user.click(
      screen.getByRole("button", { name: "Switch conversation" }),
    );
    const menu = await screen.findByRole("menu");
    const root = within(menu).getByRole("menuitem", {
      name: "Main conversation",
    });
    expect(root.getAttribute("href")).toBe(
      `/workspace/ws_1/sessions/ses_1/threads/thr_1?view=${level}`,
    );
    const branch = within(menu).getByRole("menuitem", {
      name: "Branch 1 From Main conversation",
    });
    expect(branch.getAttribute("href")).toBe(
      `/workspace/ws_1/sessions/ses_1/threads/thr_fork?view=${level}`,
    );
    expect(
      within(menu)
        .getByRole("menuitem", { name: "Branch 2 From Branch 1" })
        .getAttribute("aria-current"),
    ).toBe("page");
    expect(within(menu).getAllByRole("menuitem")).toHaveLength(3);
    await user.keyboard("{Escape}");
    rerender(
      <MemoryRouter>
        <ThreadBranches
          thread={nested}
          threads={[...threads].reverse()}
          level={level}
        />
      </MemoryRouter>,
    );
    expect(
      screen.getByRole("button", { name: "Switch conversation" }).textContent,
    ).toBe("Branch 2");
  },
);

it("links a nested fork to its immediate origin Run, not the session root or latest Run", () => {
  render(
    <MemoryRouter>
      <BranchOrigin thread={nested} threads={threads} level="chat" />
    </MemoryRouter>,
  );
  const origin = screen.getByRole("link", { name: "View origin in Branch 1" });
  expect(origin.getAttribute("href")).toBe(
    "/workspace/ws_1/sessions/ses_1/threads/thr_fork/runs/run_fork?view=chat",
  );
});

it("keeps a newly created branch identifiable while the list refreshes", () => {
  render(
    <MemoryRouter>
      <BranchOrigin thread={nested} threads={[fixtureThread()]} level="debug" />
    </MemoryRouter>,
  );
  expect(screen.getByText("Branch")).toBeTruthy();
  expect(screen.queryByText("Branch 0")).toBeNull();
  expect(
    screen
      .getByRole("link", { name: "View original run" })
      .getAttribute("href"),
  ).toBe(
    "/workspace/ws_1/sessions/ses_1/threads/thr_fork/runs/run_fork?view=debug",
  );
});
