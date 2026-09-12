// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useNavigate, useLocation } from "react-router";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { NativeWorkspace } from "./workspace";
import { FileBuffers } from "./buffer";

vi.mock("./files", () => ({
  Files: ({ path, open }: { path: string; open: (path: string) => void }) => (
    <div>
      <p>Selected {path}</p>
      <button onClick={() => open("/native/second.txt")}>
        Open second file
      </button>
    </div>
  ),
}));
vi.mock("./changes", () => ({
  Changes: ({
    path,
    selected,
    select,
  }: {
    path: string;
    selected: { path: string } | null;
    select: (value: unknown) => void;
  }) => (
    <div>
      <p>
        Repository {path} · Diff {selected?.path}
      </p>
      <button
        onClick={() =>
          select({
            repository_path: "/native",
            path: "second.txt",
            comparison: "staged",
          })
        }
      >
        Select staged diff
      </button>
    </div>
  ),
}));
vi.mock("./terminal", () => ({
  TerminalPanel: ({
    visible,
    select,
  }: {
    visible: boolean;
    select: (id: string) => void;
  }) => (
    <section hidden={!visible}>
      <button onClick={() => select("")}>Close selected terminal</button>
    </section>
  ),
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});
function setup(
  initial: string,
  sharing = true,
  metadata?: Promise<{ data: { kind: string } }>,
) {
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  const focus = vi.fn();
  const get = vi.fn(async (url: string) =>
    url === "/api/host/files/metadata" && metadata
      ? metadata
      : {
          data:
            url === "/api/status"
              ? {
                  features: {
                    host_files: sharing,
                    host_git: sharing,
                    host_terminal: sharing,
                  },
                }
              : url === "/api/projects"
                ? []
                : { kind: "file" },
        },
  );
  const transport = { client: { GET: get } } as unknown as Transport;
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  function Page() {
    const navigate = useNavigate();
    const location = useLocation();
    return (
      <NativeWorkspace onFocus={focus} unauthorized={vi.fn()}>
        <p data-testid="chat">Chat remains mounted {location.pathname}</p>
        <button onClick={() => navigate("/threads/other")}>
          Another conversation
        </button>
        <button
          onClick={() =>
            navigate("/settings/source?path=agents%2Ffixture.yaml")
          }
        >
          Open peer resource
        </button>
        <button
          onClick={() =>
            navigate(
              "/threads/current?native=changes&native_path=%2Fother-repository&diff_path=reviewed.txt&comparison=staged",
            )
          }
        >
          Open peer diff
        </button>
      </NativeWorkspace>
    );
  }
  render(
    <MemoryRouter initialEntries={[initial]}>
      <QueryClientProvider client={queries}>
        <TransportContext value={transport}>
          <FileBuffers value={new Map()}>
            <Page />
          </FileBuffers>
        </TransportContext>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { focus, get };
}
it("native deep links, file tabs and diff selection update personal focus without moving the selected conversation", async () => {
  const { focus } = setup(
    "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt",
  );
  await screen.findByText("Selected /native/first.txt");
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "file",
      path: "/native/first.txt",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Open second file" }));
  await screen.findByText("Selected /native/second.txt");
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "file",
      path: "/native/second.txt",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "first.txt" }));
  await screen.findByText("Selected /native/first.txt");
  fireEvent.pointerDown(screen.getByTestId("chat"));
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  fireEvent.click(screen.getByRole("button", { name: "Changes" }));
  fireEvent.click(screen.getByRole("button", { name: "Select staged diff" }));
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "changes",
      repository_root: "/native",
      path: "second.txt",
      comparison: "staged",
    }),
  );
  expect(screen.getByTestId("chat").textContent).toContain("/threads/current");
  fireEvent.click(screen.getByRole("button", { name: "Another conversation" }));
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  expect(screen.getByTestId("chat").textContent).toContain("/threads/other");
});
it("a native deep link cannot bypass disabled sharing or trigger metadata requests", async () => {
  const { get } = setup(
    "/?native=files&native_path=%2Fnative%2Ffirst.txt",
    false,
  );
  await screen.findByText("Native sharing disabled by this server");
  expect(get.mock.calls.some(([path]) => path.startsWith("/api/host"))).toBe(
    false,
  );
  expect(screen.queryByRole("button", { name: "Files" })).toBeNull();
});

it("a peer diff supersedes a pending file read rather than mixing repository identities", async () => {
  let finish!: (value: { data: { kind: string } }) => void;
  const metadata = new Promise<{ data: { kind: string } }>((resolve) => {
    finish = resolve;
  });
  const { get, focus } = setup(
    "/threads/current?native=files&native_path=%2Fnative%2Fslow.txt",
    true,
    metadata,
  );
  await waitFor(() =>
    expect(
      get.mock.calls.some(([url]) => url === "/api/host/files/metadata"),
    ).toBe(true),
  );
  fireEvent.click(screen.getByRole("button", { name: "Open peer diff" }));
  await screen.findByText("Repository /other-repository · Diff reviewed.txt");
  finish({ data: { kind: "directory" } });
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "changes",
      repository_root: "/other-repository",
      path: "reviewed.txt",
      comparison: "staged",
    }),
  );
  expect(
    screen.getByText("Repository /other-repository · Diff reviewed.txt"),
  ).toBeTruthy();
  expect(screen.queryByText("Opening native path…")).toBeNull();
});
it("toolbar collapse and confirmed process removal clear terminal page presence", async () => {
  const { focus } = setup("/threads/current?terminal=terminal-current");
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "terminal",
      terminal_id: "terminal-current",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Terminal" }));
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  fireEvent.click(screen.getByRole("button", { name: "Terminal" }));
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "terminal",
      terminal_id: "terminal-current",
    }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Close selected terminal" }),
  );
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
});

it("narrow peer navigation reveals the requested native or configuration view, not a hidden view behind Terminal", async () => {
  const { focus } = setup("/threads/current?terminal=terminal-current");
  await screen.findByRole("button", { name: "Terminal" });
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  fireEvent.click(screen.getByRole("button", { name: "Open peer diff" }));
  await screen.findByText("Repository /other-repository · Diff reviewed.txt");
  expect(
    screen
      .getByRole("button", { name: "Terminal" })
      .getAttribute("aria-pressed"),
  ).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Open peer resource" }));
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  expect(screen.getByTestId("chat").textContent).toContain("/settings/source");
  expect(screen.queryByLabelText("Native computer context")).toBeNull();
});
