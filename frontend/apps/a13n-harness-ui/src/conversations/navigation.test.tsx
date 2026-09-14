// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { ConversationNavigation } from "./navigation";

const fixture = vi.hoisted(() => ({
  error: null as Error | null,
  threads: vi.fn(),
  refetch: vi.fn(),
  more: vi.fn(),
}));
vi.mock("../transport/context", () => ({
  useProjects: () => ({
    data: [{ project_id: "project-one", name: "Workbench" }],
  }),
}));
vi.mock("./queries", () => ({
  useThreads: (
    query: string,
    project: string | undefined,
    archived: boolean,
  ) => {
    fixture.threads(query, project, archived);
    const rows = [
      {
        thread: {
          thread_id: "thread-one",
          title: "Review configuration",
          configuration: { project_id: "project-one" },
          root_activity: { state: "inactive" },
        },
        pending_decision: true,
      },
      {
        thread: {
          thread_id: "thread-two",
          title: "Implement settings",
          configuration: { project_id: "project-one" },
          root_activity: { state: "running" },
        },
      },
      {
        thread: {
          thread_id: "thread-three",
          title: "Check provider",
          configuration: {},
          root_activity: { state: "inactive" },
        },
        latest_operation: { status: "failed" },
      },
    ].filter((row) =>
      row.thread.title.toLowerCase().includes(query.toLowerCase()),
    );
    return {
      data: fixture.error ? undefined : { pages: [{ rows }] },
      isSuccess: !fixture.error,
      isPending: false,
      error: fixture.error,
      refetch: fixture.refetch,
      hasNextPage: !fixture.error,
      fetchNextPage: fixture.more,
    };
  },
}));
afterEach(() => {
  cleanup();
  fixture.error = null;
  vi.clearAllMocks();
});

it("collapses project groups without conflating their actions and reveals matching threads during search", async () => {
  render(
    <MemoryRouter>
      <ConversationNavigation />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  const group = screen.getByRole("button", {
    name: "Conversations in Workbench",
  });
  await user.click(group);
  await waitFor(() =>
    expect(
      screen.queryByRole("link", { name: /Review configuration/ }),
    ).toBeNull(),
  );
  expect(
    screen
      .getByRole("link", { name: "Settings for Workbench" })
      .getAttribute("href"),
  ).toBe("/projects/project-one");
  expect(
    screen.getByRole("button", { name: "New conversation in Workbench" }),
  ).toBeTruthy();
  await user.type(
    screen.getByRole("searchbox", { name: "Find conversations" }),
    "Review",
  );
  expect(
    await screen.findByRole("link", {
      name: /Review configuration.*Needs your answer/,
    }),
  ).toBeTruthy();
  expect(group.getAttribute("aria-expanded")).toBe("true");
  expect(
    screen.queryByRole("button", {
      name: "Conversations in Without a project",
    }),
  ).toBeNull();
  await user.clear(screen.getByRole("searchbox"));
  await waitFor(() =>
    expect(
      screen.queryByRole("link", { name: /Review configuration/ }),
    ).toBeNull(),
  );
  await user.click(group);
  expect(
    await screen.findByRole("link", { name: /Implement settings.*Running/ }),
  ).toBeTruthy();
  expect(
    screen.getByRole("link", { name: /Check provider.*Failed/ }),
  ).toBeTruthy();
  await user.click(screen.getByRole("checkbox", { name: "Include archived" }));
  expect(fixture.threads).toHaveBeenLastCalledWith("", undefined, true);
  await user.click(
    screen.getByRole("button", { name: "Load more conversations" }),
  );
  expect(fixture.more).toHaveBeenCalledOnce();
});

it("preserves thread action menus and avoids empty-result claims when the list fails", async () => {
  const { rerender } = render(
    <MemoryRouter>
      <ConversationNavigation />
    </MemoryRouter>,
  );
  const user = userEvent.setup();
  await user.click(
    screen.getByRole("button", { name: "Actions for Review configuration" }),
  );
  expect(
    await screen.findByRole("menuitem", { name: "Rename conversation" }),
  ).toBeTruthy();
  expect(
    screen.getByRole("menuitem", { name: "Share conversation" }),
  ).toBeTruthy();
  await user.keyboard("{Escape}");
  fixture.error = new Error("Conversations unavailable");
  rerender(
    <MemoryRouter>
      <ConversationNavigation />
    </MemoryRouter>,
  );
  await user.type(screen.getByRole("searchbox"), "missing");
  expect(screen.getByRole("alert").textContent).toContain(
    "Conversations unavailable",
  );
  expect(screen.queryByText("No matching conversations.")).toBeNull();
  expect(screen.queryByText("No conversations yet")).toBeNull();
});
