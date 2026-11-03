// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { useEffect, type ReactNode, type Ref } from "react";
import { cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { Link, MemoryRouter, Route, Routes } from "react-router";
import { SessionLayout } from "./page";
import { fixtureSession, fixtureThread } from "./transcript/fixture";

let workspaceId = "workspace";
const mounted = vi.fn(),
  disposed = vi.fn();
const session = fixtureSession();
vi.mock("../../auth/context", () => ({ useClient: () => ({}) }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({ workspace: { id: workspaceId }, can: () => true }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({ t: (key: string) => key }),
}));
vi.mock("./queries", () => ({ useSession: () => ({ data: session }) }));
vi.mock("./api", async (original) => ({
  ...(await original<typeof import("./api")>()),
  conversationQueries: (_client: unknown, workspace: string) => ({
    threads: () => ({
      queryKey: [workspace, "threads"],
      queryFn: async () => ({ items: [] }),
    }),
    thread: (id: string) => ({
      queryKey: [workspace, "thread", id],
      queryFn: async () => fixtureThread({ id, session_id: session.id }),
    }),
    run: (id: string) => ({
      queryKey: [workspace, "run", id],
      queryFn: async () => ({ id }),
    }),
    items: (id: string) => ({
      queryKey: [workspace, "items", id],
      queryFn: async () => ({ items: [] }),
    }),
    runs: (id: string) => ({
      queryKey: [workspace, "runs", id],
      queryFn: async () => ({ items: [] }),
    }),
  }),
}));
vi.mock("./message-authors", () => ({
  SessionAuthorsProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("./session-stream", () => ({
  SessionStreamProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("./transcript/debug/collapse", () => ({
  RunCollapseProvider: ({ children }: { children: ReactNode }) => children,
}));
vi.mock("./transcript", () => ({}));
vi.mock("./session-header", () => ({
  SessionHeader: (props: {
    onToggleBrowser: () => void;
    onToggleDesktop: () => void;
    browserTriggerRef: Ref<HTMLButtonElement>;
    desktopTriggerRef: Ref<HTMLButtonElement>;
    browserOpen: boolean;
    desktopOpen: boolean;
  }) => (
    <>
      <button
        ref={props.browserTriggerRef}
        aria-expanded={props.browserOpen}
        onClick={props.onToggleBrowser}
      >
        Browser
      </button>
      <button
        ref={props.desktopTriggerRef}
        aria-expanded={props.desktopOpen}
        onClick={props.onToggleDesktop}
      >
        Desktop
      </button>
    </>
  ),
}));
vi.mock("./transcript/environment-desktop", () => ({
  ThreadDesktopPanel: ({
    threadId,
    onClose,
  }: {
    threadId: string;
    onClose: () => void;
  }) => {
    useEffect(() => {
      mounted(threadId);
      return () => {
        disposed(threadId);
      };
    }, [threadId]);
    return <button onClick={onClose}>Close desktop panel</button>;
  },
}));
vi.mock("../browsers/thread-browser", () => ({
  ThreadBrowserPanel: ({ onClose }: { onClose: () => void }) => (
    <button onClick={onClose}>Close browser panel</button>
  ),
}));
afterEach(() => {
  cleanup();
  workspaceId = "workspace";
  vi.clearAllMocks();
});

function setup() {
  const cache = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const tree = () => (
    <QueryClientProvider client={cache}>
      <MemoryRouter
        initialEntries={[`/sessions/${session.id}/threads/first/runs/one`]}
      >
        <Link to={`/sessions/${session.id}/threads/first/runs/two?view=debug`}>
          Next run
        </Link>
        <Link to={`/sessions/${session.id}/threads/first/runs/one?view=chat`}>
          Inspect previous run
        </Link>
        <Link to={`/sessions/${session.id}/threads/second/runs/three`}>
          Other thread
        </Link>
        <Routes>
          <Route
            path="/sessions/:sessionId/threads/:threadId/runs/:runId"
            element={<SessionLayout />}
          />
        </Routes>
      </MemoryRouter>
    </QueryClientProvider>
  );
  return { ...render(tree()), tree };
}

it("keeps Desktop across Run navigation, makes panels exclusive, and restores close focus", async () => {
  const user = userEvent.setup();
  setup();
  await user.click(screen.getByRole("button", { name: "Desktop" }));
  await screen.findByRole("complementary", { name: "Desktop" });
  await user.click(screen.getByRole("link", { name: "Next run" }));
  await user.click(screen.getByRole("link", { name: "Inspect previous run" }));
  expect(mounted).toHaveBeenCalledTimes(1);
  expect(disposed).not.toHaveBeenCalled();
  await user.click(screen.getByRole("button", { name: "Close desktop panel" }));
  expect(screen.queryByRole("complementary", { name: "Desktop" })).toBeNull();
  expect(screen.getByRole("button", { name: "Desktop" })).toBe(
    document.activeElement,
  );
  await user.click(screen.getByRole("button", { name: "Browser" }));
  expect(screen.getByRole("complementary", { name: "Browser" })).not.toBeNull();
  await user.click(screen.getByRole("button", { name: "Desktop" }));
  expect(screen.queryByRole("complementary", { name: "Browser" })).toBeNull();
  expect(screen.getByRole("complementary", { name: "Desktop" })).not.toBeNull();
  await user.click(screen.getByRole("button", { name: "Browser" }));
  expect(screen.queryByRole("complementary", { name: "Desktop" })).toBeNull();
});

it("disposes Desktop on Thread or workspace changes without reopening an old selection", async () => {
  const user = userEvent.setup();
  const view = setup();
  await user.click(screen.getByRole("button", { name: "Desktop" }));
  await screen.findByRole("complementary", { name: "Desktop" });
  await user.click(screen.getByRole("link", { name: "Other thread" }));
  expect(disposed).toHaveBeenCalledWith("first");
  await user.click(screen.getByRole("link", { name: "Inspect previous run" }));
  expect(screen.queryByRole("complementary", { name: "Desktop" })).toBeNull();
  await user.click(screen.getByRole("button", { name: "Desktop" }));
  await screen.findByRole("complementary", { name: "Desktop" });
  workspaceId = "another-workspace";
  view.rerender(view.tree());
  await waitFor(() =>
    expect(screen.queryByRole("complementary", { name: "Desktop" })).toBeNull(),
  );
  expect(disposed).toHaveBeenCalledTimes(2);
});
