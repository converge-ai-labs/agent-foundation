// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { useContext, useEffect, type ReactNode } from "react";
import { ReturnToChat } from "./capture";
import { ToolCall } from "../conversations/tool-call";
import { MessageText } from "../conversations/message-text";
import {
  cleanup,
  act,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter, useNavigate, useLocation } from "react-router";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { NativeWorkspace } from "./workspace";
import { FileBuffers, joinPath } from "./buffer";

const live = vi.hoisted(() => ({
  display: { browserActivityRunId: undefined as string | undefined },
}));
vi.mock("../conversations/live-threads", () => ({ useLiveThread: () => live }));
vi.mock("./files", () => ({
  Files: ({
    path,
    directory,
    open,
  }: {
    path: string;
    directory: string;
    open: (path: string) => void;
  }) => (
    <div>
      <p>Folder {directory}</p>
      <p>Selected {path}</p>
      <button onClick={() => open("/native/second.txt")}>
        Open second file
      </button>
      <button onClick={() => open(joinPath(directory, "nested"))}>
        Open child folder
      </button>
    </div>
  ),
}));
vi.mock("./file-view", () => ({
  FileView: ({ path }: { path: string }) => <p>Editor {path}</p>,
}));
vi.mock("./changes", () => ({
  DiffView: ({
    selection,
    openFile,
  }: {
    selection: { path: string };
    openFile: (path: string) => void;
  }) => (
    <div>
      <p>Patch {selection.path}</p>
      <button onClick={() => openFile("/native/deleted.txt")}>
        Open diff file
      </button>
    </div>
  ),
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
    directory,
    projectId,
    selected,
    select,
    onActive,
    request,
  }: {
    request?: { cwd: string; projectId: string };
    visible: boolean;
    directory: string;
    projectId: string;
    selected: string;
    select: (id: string) => void;
    onActive: (id: string) => void;
  }) => {
    useEffect(() => {
      onActive(
        visible && projectId && selected === "terminal-current" ? selected : "",
      );
    }, [visible, projectId, selected, onActive]);
    return (
      <section aria-label="Terminal panel" hidden={!visible}>
        <p>
          Terminal context {projectId} · {directory}
        </p>
        {request && (
          <p>
            New terminal request {request.projectId} · {request.cwd}
          </p>
        )}
        <button onClick={() => select("")}>Close selected terminal</button>
      </section>
    );
  },
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
  live.display.browserActivityRunId = undefined;
});
function setup(
  initial: string,
  sharing = true,
  metadata?: Promise<{ data: { kind: string } }>,
  currentProject: string | null = "project-one",
  otherProject: string | null = currentProject,
  currentRoots: string[] = ["/native"],
  content?: ReactNode,
  buffers = new Map(),
) {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      disconnect() {}
    },
  );
  const focus = vi.fn();
  const get = vi.fn(
    async (
      url: string,
      options?: {
        params?: { path?: { thread_id?: string }; query?: { path?: string } };
      },
    ) =>
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
                      browser: sharing,
                    },
                  }
                : url === "/api/projects"
                  ? [
                      {
                        project_id: "project-unrelated",
                        name: "Unrelated",
                        roots: ["/unrelated"],
                      },
                      {
                        project_id: "project-one",
                        name: "Current project",
                        roots: initial.startsWith("/threads/")
                          ? ["/changed-project-root"]
                          : currentRoots,
                      },
                      {
                        project_id: "project-two",
                        name: "Other project",
                        roots: ["/other"],
                      },
                    ]
                  : url === "/api/threads/{thread_id}"
                    ? {
                        thread: {
                          title: "Current chat",
                          configuration: {
                            local_roots:
                              options?.params?.path?.thread_id === "other"
                                ? otherProject === "project-two"
                                  ? ["/other"]
                                  : currentRoots
                                : currentProject
                                  ? currentRoots
                                  : [],
                            project_id:
                              options?.params?.path?.thread_id === "other"
                                ? otherProject
                                : currentProject,
                          },
                        },
                      }
                    : {
                        kind: options?.params?.query?.path?.endsWith(".txt")
                          ? "file"
                          : "directory",
                      },
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
      <>
        <NativeWorkspace onFocus={focus} unauthorized={vi.fn()}>
          <p data-testid="chat">Chat remains mounted {location.pathname}</p>
          {content}
        </NativeWorkspace>
        <button
          onClick={() =>
            navigate(
              "/threads/current?native=changes&native_path=%2Fother-repository",
            )
          }
        >
          Open peer repository
        </button>
        <button onClick={() => navigate("/threads/other")}>Another chat</button>
        <button onClick={() => navigate("/threads/current")}>
          Current chat
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
      </>
    );
  }
  render(
    <MemoryRouter initialEntries={[initial]}>
      <QueryClientProvider client={queries}>
        <TransportContext value={transport}>
          <FileBuffers value={buffers}>
            <Page />
          </FileBuffers>
        </TransportContext>
      </QueryClientProvider>
    </MemoryRouter>,
  );
  return { focus, get, queries };
}
async function chooseView(name: "Files" | "Changes" | "Browser" | "Terminal") {
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "New tab" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  const existing = screen.queryByRole("tab", { name });
  if (existing) {
    fireEvent.click(existing);
    return;
  }
  if (!screen.queryByRole("button", { name }))
    fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  fireEvent.click(await screen.findByRole("button", { name }));
}

function expandSupportingPanel() {
  for (let i = 0; i < 25; i++) {
    const splitter = screen.queryByRole("separator", {
      name: "Resize supporting panel",
    });
    if (!splitter) break;
    fireEvent.keyDown(splitter, { key: "ArrowLeft" });
  }
}
function restoreSideBySide() {
  fireEvent.click(
    screen.getByRole("button", { name: "Close supporting panel" }),
  );
  fireEvent.click(
    screen.getByRole("button", { name: "Open supporting panel" }),
  );
}

it("adds files through the new-tab picker in one strip and closes views without discarding private buffers", async () => {
  const dirty = { dirty: true, text: "Keep this private edit" };
  const buffers = new Map([["/native/first.txt", dirty]]);
  setup(
    "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt",
    true,
    undefined,
    "project-one",
    "project-one",
    ["/native"],
    <textarea aria-label="Private draft" defaultValue="Keep my draft" />,
    buffers,
  );
  await screen.findByText("Editor /native/first.txt");
  const conversation = screen.getByTestId("chat");
  const draft = screen.getByLabelText("Private draft");
  expect(screen.queryByRole("navigation", { name: "Open files" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Close file explorer" }),
  ).toBeNull();
  expect(screen.queryByRole("tab", { name: "Files" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  expect(
    screen.getByRole("tab", { name: "New tab", selected: true }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Files" }));
  fireEvent.click(screen.getByRole("button", { name: "Open second file" }));
  await screen.findByText("Editor /native/second.txt");
  const strip = screen.getByRole("tablist", { name: "Workspace views" });
  expect(within(strip).getAllByRole("tab")).toHaveLength(2);
  expect(
    screen.getByRole("tab", { name: "second.txt", selected: true }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("tab", { name: "first.txt · Unsaved" }));
  const editor = await screen.findByText("Editor /native/first.txt");
  fireEvent.click(
    screen.getByRole("button", { name: "Close tab: second.txt" }),
  );
  expect(screen.getByText("Editor /native/first.txt")).toBe(editor);
  fireEvent.click(
    screen.getByRole("button", { name: "Close tab: first.txt · Unsaved" }),
  );
  expect(
    screen.getByRole("tab", { name: "New tab", selected: true }),
  ).toBeTruthy();
  expect(screen.getByRole("heading", { name: "Open a new tab" })).toBeTruthy();
  expect(buffers.get("/native/first.txt")).toBe(dirty);
  expect(screen.getByTestId("chat")).toBe(conversation);
  expect(screen.getByLabelText("Private draft")).toBe(draft);
});

it("opening a new tab cancels a pending file open and its late response cannot replace the picker", async () => {
  let finish!: (value: { data: { kind: string } }) => void;
  const metadata = new Promise<{ data: { kind: string } }>((resolve) => {
    finish = resolve;
  });
  setup(
    "/threads/current?native=files&native_path=%2Fnative%2Fslow.txt",
    true,
    metadata,
  );
  await screen.findByText("Opening…");
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  await act(async () => {
    finish({ data: { kind: "file" } });
  });
  expect(screen.queryByRole("tab", { name: "slow.txt" })).toBeNull();
  expect(
    screen.getByRole("tab", { name: "New tab", selected: true }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Files" })).toBeTruthy();
});

it("an initial file link opens only its resource tab after the selected Project resolves", async () => {
  setup("/?project=project-one&native=files&native_path=%2Fnative%2Ffirst.txt");
  await screen.findByText("Editor /native/first.txt");
  expect(
    within(
      screen.getByRole("tablist", { name: "Workspace views" }),
    ).getAllByRole("tab"),
  ).toHaveLength(1);
  expect(
    screen.getByRole("tab", { name: "first.txt", selected: true }),
  ).toBeTruthy();
});

it("an initial directory link navigates the Project's initial tab to Files", async () => {
  setup("/?project=project-one&native=files&native_path=%2Fnative%2Fsrc");
  await screen.findByText("Folder /native/src");
  expect(screen.getAllByRole("tab")).toHaveLength(1);
  expect(
    screen.getByRole("tab", { name: "Files", selected: true }),
  ).toBeTruthy();
});

it("restores each Project's resource tabs without mixing their locations", async () => {
  setup("/threads/current", true, undefined, "project-one", "project-two");
  await chooseView("Files");
  fireEvent.click(screen.getByRole("button", { name: "Open second file" }));
  await screen.findByText("Editor /native/second.txt");
  fireEvent.click(screen.getByRole("button", { name: "Another chat" }));
  await chooseView("Browser");
  expect(screen.queryByRole("tab", { name: "second.txt" })).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Current chat" }));
  await screen.findByText("Editor /native/second.txt");
  expect(
    screen.getByRole("tab", { name: "second.txt", selected: true }),
  ).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Browser" })).toBeNull();
});

it("native deep links, file tabs and diff selection update personal focus without moving the selected conversation", async () => {
  const { focus } = setup(
    "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt",
  );
  await screen.findByText("Editor /native/first.txt");
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "file",
      path: "/native/first.txt",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  fireEvent.click(screen.getByRole("button", { name: "Files" }));
  fireEvent.click(screen.getByRole("button", { name: "Open second file" }));
  await screen.findByText("Editor /native/second.txt");
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "file",
      path: "/native/second.txt",
    }),
  );
  fireEvent.click(screen.getByRole("tab", { name: "first.txt" }));
  await screen.findByText("Editor /native/first.txt");
  fireEvent.pointerDown(screen.getByTestId("chat"));
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  await chooseView("Changes");
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
  fireEvent.click(screen.getByRole("button", { name: "Another chat" }));
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  expect(screen.getByTestId("chat").textContent).toContain("/threads/other");
});
it("opens an assistant Host file link in the current chat drawer", async () => {
  setup(
    "/threads/current",
    true,
    undefined,
    "project-one",
    "project-one",
    ["/native"],
    <MessageText text="[Open report](/threads/current?native=files&native_path=%2Fnative%2Freport.txt)" />,
  );
  await chooseView("Files");
  await screen.findByText("Folder /native");
  fireEvent.pointerDown(screen.getByTestId("chat"));
  fireEvent.click(screen.getByRole("link", { name: "Open report" }));
  await screen.findByText("Editor /native/report.txt");
  expect(screen.getByTestId("chat").textContent).toContain("/threads/current");
});

it("a native deep link cannot bypass disabled sharing or trigger metadata requests", async () => {
  const { get } = setup(
    "/?native=files&native_path=%2Fnative%2Ffirst.txt",
    false,
  );
  await waitFor(() =>
    expect(get.mock.calls.some(([path]) => path === "/api/status")).toBe(true),
  );
  expect(get.mock.calls.some(([path]) => path.startsWith("/api/host"))).toBe(
    false,
  );
  expect(screen.queryByRole("button", { name: "Files" })).toBeNull();
});

it.each(["sidebar", "tabs"])(
  "disabled supporting views leave Chat unobstructed with the %s preference",
  async (layout) => {
    localStorage.setItem("a13n-harness-ui.native-layout", layout);
    setup("/threads/current", false);
    await screen.findByText("Current project");
    expect(screen.getByTestId("chat").closest("[hidden]")).toBeNull();
    expect(
      screen.queryByRole("navigation", { name: "Workbench views" }),
    ).toBeNull();
    expect(screen.queryByRole("tabpanel")).toBeNull();
    expect(
      screen.queryByRole("button", { name: /supporting panel/ }),
    ).toBeNull();
    expect(
      screen.queryByRole("separator", { name: "Resize supporting panel" }),
    ).toBeNull();
  },
);

it("disabling supporting views reveals Chat even when a resource tab was active", async () => {
  localStorage.setItem("a13n-harness-ui.native-layout", "tabs");
  const { queries } = setup(
    "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt",
  );
  await screen.findByText("Editor /native/first.txt");
  expect(screen.getByTestId("chat").closest("[hidden]")).not.toBeNull();
  act(() => {
    queries.setQueryData(["status"], {
      features: {
        host_files: false,
        host_git: false,
        host_terminal: false,
        browser: false,
      },
    });
  });
  await waitFor(() =>
    expect(screen.getByTestId("chat").closest("[hidden]")).toBeNull(),
  );
  expect(screen.queryByRole("tabpanel")).toBeNull();
  expect(
    screen.queryByRole("navigation", { name: "Workbench views" }),
  ).toBeNull();
});

it("cross-project file links use the destination root and preserve its restored tabs", async () => {
  function CrossProjectLink() {
    const navigate = useNavigate();
    return (
      <button
        onClick={() =>
          navigate(
            "/threads/other?native=files&native_path=%2Fother%2Ffirst.txt",
          )
        }
      >
        Cross project file
      </button>
    );
  }
  setup(
    "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt",
    true,
    undefined,
    "project-one",
    "project-two",
    ["/native"],
    <CrossProjectLink />,
  );
  await screen.findByText("Editor /native/first.txt");
  fireEvent.click(screen.getByRole("button", { name: "Cross project file" }));
  await screen.findByText("Editor /other/first.txt");
  expect(screen.getAllByRole("tab")).toHaveLength(1);
  await chooseView("Changes");
  expect(screen.getByText(/Repository .* · Diff/).textContent).toBe(
    "Repository /other · Diff ",
  );
  fireEvent.click(screen.getByRole("button", { name: "Current chat" }));
  await screen.findByText("Editor /native/first.txt");
  fireEvent.click(screen.getByRole("button", { name: "Cross project file" }));
  await screen.findByText("Editor /other/first.txt");
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  await chooseView("Changes");
  expect(screen.getByText(/Repository .* · Diff/).textContent).toBe(
    "Repository /other · Diff ",
  );
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
  await screen.findByText("Patch reviewed.txt");
  finish({ data: { kind: "directory" } });
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "changes",
      repository_root: "/other-repository",
      path: "reviewed.txt",
      comparison: "staged",
    }),
  );
  expect(screen.getByText("Patch reviewed.txt")).toBeTruthy();
  expect(screen.queryByText("Opening…")).toBeNull();
});
it("shortcut collapse and confirmed process removal clear terminal page presence", async () => {
  const { focus } = setup("/threads/current?terminal=terminal-current");
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "terminal",
      terminal_id: "terminal-current",
    }),
  );
  fireEvent.keyDown(window, { code: "Backquote", key: "`", ctrlKey: true });
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  fireEvent.keyDown(window, { code: "Backquote", key: "`", ctrlKey: true });
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
  await screen.findByRole("tab", { name: "Terminal" });
  vi.stubGlobal("matchMedia", () => ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  }));
  fireEvent.click(screen.getByRole("button", { name: "Open peer diff" }));
  await screen.findByText("Patch reviewed.txt");
  expect(screen.getByLabelText("File explorer")).toBeTruthy();
  expect(screen.getByLabelText("Terminal panel").hasAttribute("hidden")).toBe(
    true,
  );
  fireEvent.click(screen.getByRole("button", { name: "Open peer resource" }));
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  expect(screen.getByTestId("chat").textContent).toContain("/settings/source");
  expect(screen.queryByLabelText("File explorer")).toBeNull();
  expect(screen.queryByRole("button", { name: "Files" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Terminal" })).toBeNull();
});

it("Settings supersedes a pending file open and returning to Chat cannot be stolen by its late response", async () => {
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
  fireEvent.click(screen.getByRole("button", { name: "Open peer resource" }));
  expect(screen.queryByLabelText("Workbench views")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Another chat" }));
  finish({ data: { kind: "file" } });
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  expect(screen.queryByRole("button", { name: "slow.txt" })).toBeNull();
  expect(screen.getByTestId("chat").parentElement?.hidden).toBe(false);
});

it("switches layouts and tabs without remounting the conversation or rereading the open file", async () => {
  localStorage.setItem("a13n-harness-ui.native-layout", "tabs");
  const { get, focus } = setup(
    "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt",
    true,
    undefined,
    "project-one",
    "project-one",
    ["/native"],
    <textarea aria-label="Private draft" defaultValue="Keep this draft" />,
  );
  const editor = await screen.findByText("Editor /native/first.txt");
  const conversation = screen.getByTestId("chat");
  const draft = screen.getByLabelText("Private draft");
  const reads = get.mock.calls.filter(
    ([url]) => url === "/api/host/files/metadata",
  ).length;
  expect(
    conversation.closest('[role="tabpanel"]')?.hasAttribute("hidden"),
  ).toBe(true);
  fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  expect(screen.getByTestId("chat")).toBe(conversation);
  expect(screen.getByLabelText("Private draft")).toBe(draft);
  expect(editor.closest('[role="tabpanel"]')?.hasAttribute("hidden")).toBe(
    true,
  );
  fireEvent.click(screen.getByRole("tab", { name: "first.txt" }));
  expect(screen.getByText("Editor /native/first.txt")).toBe(editor);
  expect(
    get.mock.calls.filter(([url]) => url === "/api/host/files/metadata"),
  ).toHaveLength(reads);
  restoreSideBySide();
  expect(screen.getByRole("tablist", { name: "Workspace views" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Chat" })).toBeNull();
  expect(
    screen.getByRole("tab", { name: "first.txt", selected: true }),
  ).toBeTruthy();
  expect(screen.getByTestId("chat")).toBe(conversation);
  expect(screen.getByText("Editor /native/first.txt")).toBe(editor);
  expect(
    screen.getByRole("separator", { name: "Resize supporting panel" }),
  ).toBeTruthy();
  expect(localStorage.getItem("a13n-harness-ui.native-layout")).toBe("sidebar");
  expect(
    screen.queryByRole("button", { name: "Open panel as tab" }),
  ).toBeNull();
  expandSupportingPanel();
  expect(
    screen.getByRole("tab", { name: "first.txt", selected: true }),
  ).toBeTruthy();
  expect(localStorage.getItem("a13n-harness-ui.native-layout")).toBe("tabs");
  await chooseView("Browser");
  fireEvent.click(screen.getByRole("tab", { name: "first.txt" }));
  const files = screen.getByRole("tab", { name: "first.txt" });
  files.focus();
  fireEvent.keyDown(files, { key: "Home" });
  expect(document.activeElement).toBe(
    screen.getByRole("tab", { name: "Chat", selected: true }),
  );
  fireEvent.keyDown(document.activeElement!, { key: "End" });
  expect(document.activeElement).toBe(
    screen.getByRole("tab", { name: "Browser", selected: true }),
  );
  expect(screen.getByRole("tabpanel", { name: "Browser" })).toBeTruthy();
  expect(
    screen.queryByRole("button", { name: "Dock panel to the side" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
  expect(screen.queryByRole("tabpanel", { name: "Browser" })).toBeNull();
});

it("shows tab overflow hints only on edges with more content after scrolling or resizing", async () => {
  setup("/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt");
  const measurements: Array<() => void> = [];
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(measure: () => void) {
        measurements.push(measure);
      }
      observe() {}
      disconnect() {}
    },
  );
  await screen.findByText("Editor /native/first.txt");
  const tabs = screen.getByRole("tablist", { name: "Workspace views" });
  let available = 160;
  Object.defineProperties(tabs, {
    clientWidth: { get: () => available },
    scrollWidth: { value: 300 },
  });
  act(() => measurements.forEach((measure) => measure()));
  expect(tabs.hasAttribute("data-overflow-left")).toBe(false);
  expect(tabs.hasAttribute("data-overflow-right")).toBe(true);
  tabs.scrollLeft = 60;
  fireEvent.scroll(tabs);
  expect(tabs.hasAttribute("data-overflow-left")).toBe(true);
  expect(tabs.hasAttribute("data-overflow-right")).toBe(true);
  tabs.scrollLeft = 140;
  fireEvent.scroll(tabs);
  expect(tabs.hasAttribute("data-overflow-right")).toBe(false);
  available = 400;
  tabs.scrollLeft = 0;
  act(() => measurements.forEach((measure) => measure()));
  expect(tabs.hasAttribute("data-overflow-left")).toBe(false);
  expect(tabs.hasAttribute("data-overflow-right")).toBe(false);
});

it("keeps one drag reversible across tabs, split, and closed until release", async () => {
  vi.stubGlobal(
    "PointerEvent",
    class extends MouseEvent {
      pointerId: number;
      constructor(type: string, init: PointerEventInit) {
        super(type, init);
        this.pointerId = init.pointerId ?? 0;
      }
    },
  );
  setup("/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt");
  const editor = await screen.findByText("Editor /native/first.txt");
  const conversation = screen.getByTestId("chat");
  const prepareDrag = () => {
    const separator = screen.getByRole("separator", {
      name: "Resize supporting panel",
    });
    Object.defineProperty(separator.parentElement, "clientWidth", {
      configurable: true,
      value: 1000,
    });
    separator.parentElement!.setPointerCapture = vi.fn();
    fireEvent.pointerDown(separator, { clientX: 560, pointerId: 1 });
    return separator;
  };
  const separator = prepareDrag();
  const layout = separator.parentElement!;
  expect(layout.setPointerCapture).toHaveBeenCalledWith(1);
  fireEvent.pointerMove(layout, { clientX: 260, pointerId: 2 });
  expect(separator.getAttribute("aria-valuenow")).toBe("44");
  fireEvent.pointerMove(separator, { clientX: 320, pointerId: 1 });
  expect(separator.getAttribute("aria-valuenow")).toBe("68");
  fireEvent.pointerMove(separator, { clientX: 260, pointerId: 1 });
  expect(screen.queryByRole("separator")).toBeNull();
  expect(
    screen.getByRole("tab", { name: "first.txt", selected: true }),
  ).toBeTruthy();
  expect(
    conversation.closest('[role="tabpanel"]')?.hasAttribute("hidden"),
  ).toBe(true);
  expect(screen.getByText("Editor /native/first.txt")).toBe(editor);
  fireEvent.pointerMove(layout, { clientX: 500, pointerId: 1 });
  expect(screen.queryByRole("tab", { name: "Chat" })).toBeNull();
  expect(screen.getByRole("separator").getAttribute("aria-valuenow")).toBe(
    "50",
  );
  fireEvent.pointerMove(layout, { clientX: 760, pointerId: 1 });
  expect(screen.queryByRole("separator")).toBeNull();
  expect(
    screen.queryByRole("navigation", { name: "Workbench views" }),
  ).toBeNull();
  expect(screen.getByTestId("chat")).toBe(conversation);
  expect(screen.queryByRole("tabpanel", { name: "first.txt" })).toBeNull();
  // One held pointer can cross both edges repeatedly, even with no separator.
  fireEvent.pointerMove(layout, { clientX: 560, pointerId: 1 });
  expect(screen.getByRole("separator").getAttribute("aria-valuenow")).toBe(
    "44",
  );
  fireEvent.pointerMove(layout, { clientX: 260, pointerId: 1 });
  expect(screen.getByRole("tab", { name: "Chat" })).toBeTruthy();
  fireEvent.pointerMove(layout, { clientX: 760, pointerId: 1 });
  expect(screen.queryByRole("separator")).toBeNull();
  expect(screen.queryByRole("tab", { name: "Chat" })).toBeNull();
  fireEvent.pointerUp(layout, { clientX: 760, pointerId: 1 });
  fireEvent.pointerMove(layout, { clientX: 560, pointerId: 1 });
  expect(screen.queryByRole("separator")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Open supporting panel" }),
  );
  expect(
    screen.getByRole("tab", { name: "first.txt", selected: true }),
  ).toBeTruthy();
  expect(screen.getByRole("separator").getAttribute("aria-valuenow")).toBe(
    "44",
  );
  expect(screen.getByText("Editor /native/first.txt")).toBe(editor);
});

it("ignores superseded animation updates during a drag and ends on pointer cancellation", async () => {
  vi.stubGlobal("PointerEvent", MouseEvent);
  setup("/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt");
  await screen.findByText("Editor /native/first.txt");
  const separator = screen.getByRole("separator");
  const layout = separator.parentElement!;
  Object.defineProperty(layout, "clientWidth", { value: 1000 });
  layout.setPointerCapture = vi.fn();
  const updates: Array<() => void> = [];
  const motions: Set<string>[] = [];
  const original = Object.getOwnPropertyDescriptor(
    document,
    "startViewTransition",
  );
  Object.defineProperty(document, "startViewTransition", {
    configurable: true,
    value: (update: () => void) => {
      updates.push(update);
      const types = new Set<string>();
      motions.push(types);
      return { ready: Promise.resolve(), skipTransition: vi.fn(), types };
    },
  });
  try {
    fireEvent.pointerDown(separator, { clientX: 560 });
    fireEvent.pointerMove(layout, { clientX: 260 });
    fireEvent.pointerMove(layout, { clientX: 760 });
    fireEvent.pointerMove(layout, { clientX: 500 });
    fireEvent.pointerMove(layout, { clientX: 460 });
    expect(updates).toHaveLength(3);
    expect(motions[1]?.has("panel-close")).toBe(true);
    act(() => updates[2]!());
    act(() => updates[1]!());
    act(() => updates[0]!());
    expect(screen.queryByRole("tab", { name: "Chat" })).toBeNull();
    expect(screen.getByRole("separator").getAttribute("aria-valuenow")).toBe(
      "54",
    );
    fireEvent.pointerCancel(layout);
    fireEvent.pointerMove(layout, { clientX: 260 });
    expect(updates).toHaveLength(3);
    expect(layout.hasAttribute("data-resizing")).toBe(false);
  } finally {
    if (original)
      Object.defineProperty(document, "startViewTransition", original);
    else Reflect.deleteProperty(document, "startViewTransition");
  }
});

it("uses tabs in narrow windows without replacing the saved desktop layout", async () => {
  setup("/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt");
  await screen.findByText("Editor /native/first.txt");
  vi.stubGlobal("matchMedia", () => ({
    matches: true,
    addEventListener() {},
    removeEventListener() {},
  }));
  fireEvent.click(screen.getByRole("button", { name: "Back to files" }));
  fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
  expect(
    screen
      .getByTestId("chat")
      .closest('[role="tabpanel"]')
      ?.hasAttribute("hidden"),
  ).toBe(false);
  fireEvent.click(screen.getByRole("tab", { name: "Files" }));
  expect(
    screen.getByRole("tab", { name: "Files", selected: true }),
  ).toBeTruthy();
  expect(localStorage.getItem("a13n-harness-ui.native-layout")).toBe("sidebar");
  restoreSideBySide();
  expect(screen.getByRole("tablist", { name: "Workspace views" })).toBeTruthy();
  expect(screen.queryByRole("tab", { name: "Chat" })).toBeNull();
  expect(
    screen.getByRole("button", { name: "Close supporting panel" }),
  ).toBeTruthy();
});

it("retains resource tabs through Settings and direct Chat interaction updates focus", async () => {
  const { focus } = setup(
    "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt",
  );
  await screen.findByText("Editor /native/first.txt");
  fireEvent.pointerDown(screen.getByTestId("chat"));
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));

  fireEvent.click(screen.getByRole("button", { name: "Open peer resource" }));
  expect(screen.queryByLabelText("Open files")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Another chat" }));
  fireEvent.click(screen.getByRole("tab", { name: "first.txt" }));
  await screen.findByText("Editor /native/first.txt");
});

it("Chat and chat navigation detach the Terminal viewer while retaining its tab", async () => {
  localStorage.setItem("a13n-harness-ui.native-layout", "tabs");
  setup("/threads/current?terminal=terminal-current");
  await screen.findByRole("tab", { name: "Terminal" });
  const terminal = screen.getByLabelText("Terminal panel");
  fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
  expect(terminal.hasAttribute("hidden")).toBe(true);
  await chooseView("Terminal");
  expect(terminal.hasAttribute("hidden")).toBe(false);
  fireEvent.click(screen.getByRole("button", { name: "Another chat" }));
  expect(terminal.hasAttribute("hidden")).toBe(true);
  expect(screen.getByRole("tab", { name: "Terminal" })).toBeTruthy();
});

it.each(["Chat", "Conversation tab", "diff"])(
  "an explicit %s view supersedes a pending file open without a route change",
  async (target) => {
    if (target === "Conversation tab")
      localStorage.setItem("a13n-harness-ui.native-layout", "tabs");
    let finish!: (value: { data: { kind: string } }) => void;
    const metadata = new Promise<{ data: { kind: string } }>((resolve) => {
      finish = resolve;
    });
    const { focus, get } = setup(
      "/threads/current?native=files&native_path=%2Fnative%2Fslow.txt",
      true,
      metadata,
    );
    await waitFor(() =>
      expect(
        get.mock.calls.some(([url]) => url === "/api/host/files/metadata"),
      ).toBe(true),
    );
    if (target === "Chat") fireEvent.pointerDown(screen.getByTestId("chat"));
    else if (target === "Conversation tab")
      fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
    else {
      await chooseView("Changes");
      fireEvent.click(
        screen.getByRole("button", { name: "Select staged diff" }),
      );
    }
    finish({ data: { kind: "file" } });
    await waitFor(() =>
      expect(focus).toHaveBeenLastCalledWith(
        target !== "diff"
          ? null
          : {
              kind: "changes",
              repository_root: "/native",
              path: "second.txt",
              comparison: "staged",
            },
      ),
    );
    expect(screen.queryByText("Editor /native/slow.txt")).toBeNull();
    if (target !== "diff")
      expect(screen.getByTestId("chat").closest("[hidden]")).toBeNull();
    else expect(screen.getByText("Patch second.txt")).toBeTruthy();
  },
);

it.each(["file", "diff"])(
  "a repository-only peer link replaces a prior %s editor with its actual Changes explorer",
  async (view) => {
    const { focus } = setup(
      view === "file"
        ? "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt"
        : "/threads/current?native=changes&native_path=%2Fnative&diff_path=first.txt&comparison=staged",
    );
    await screen.findByText(
      view === "file" ? "Editor /native/first.txt" : "Patch first.txt",
    );
    fireEvent.click(
      screen.getByRole("button", { name: "Open peer repository" }),
    );
    await screen.findByText("Repository /other-repository · Diff");
    expect(screen.queryByText("Editor /native/first.txt")).toBeNull();
    expect(screen.queryByText("Patch first.txt")).toBeNull();
    await waitFor(() =>
      expect(focus).toHaveBeenLastCalledWith({
        kind: "changes",
        repository_root: "/other-repository",
      }),
    );
  },
);
it.each(["Browser", "Terminal"] as const)(
  "a pending or failed Host file link retains the active %s tab and exposes feedback",
  async (pane) => {
    const { get } = setup(
      "/threads/current",
      true,
      undefined,
      "project-one",
      "project-one",
      ["/native"],
      <MessageText text="[Open report](/threads/current?native=files&native_path=%2Fnative%2Freport.txt)" />,
    );
    await chooseView(pane);
    let reject!: (error: Error) => void;
    const pending = new Promise<{ data: { kind: string } }>(
      (_resolve, fail) => {
        reject = fail;
      },
    );
    get.mockImplementationOnce(() => pending);
    fireEvent.click(screen.getByRole("link", { name: "Open report" }));
    const panel = screen.getByRole("tabpanel", { name: pane });
    await within(panel).findByText("Opening…");
    expect(
      screen.getByRole("tab", { name: pane, selected: true }),
    ).toBeTruthy();
    reject(new Error("File unavailable"));
    await within(panel).findByText("File unavailable");
    expect(
      screen.getByRole("tab", { name: pane, selected: true }),
    ).toBeTruthy();
    expect(panel.closest("[hidden]")).toBeNull();
    expect(screen.queryByText("Opening…")).toBeNull();
    expect(screen.queryByRole("tab", { name: "report.txt" })).toBeNull();
  },
);

it("shows pending and failed file opens beside the retained diff instead of hiding feedback in the explorer", async () => {
  const { get } = setup(
    "/threads/current?native=changes&native_path=%2Fnative&diff_path=deleted.txt&comparison=staged",
  );
  await screen.findByText("Patch deleted.txt");
  let reject!: (error: Error) => void;
  const pending = new Promise<{ data: { kind: string } }>((_resolve, fail) => {
    reject = fail;
  });
  get.mockImplementationOnce(() => pending);
  fireEvent.click(screen.getByRole("button", { name: "Open diff file" }));
  await screen.findByText("Opening…");
  reject(new Error("The file was deleted"));
  await screen.findByText("The file was deleted");
  expect(screen.getByText("Patch deleted.txt")).toBeTruthy();
  expect(
    screen
      .getByRole("tab", { name: "deleted.txt · Changes" })
      .getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.queryByText("Opening…")).toBeNull();
});

it("Files, Changes and new-terminal context follow Thread roots instead of changed Project roots or remembered folders", async () => {
  localStorage.setItem("a13n-harness-ui.native-directory", "/unrelated");
  setup("/threads/current", true, undefined, "project-one", "project-two");
  await chooseView("Files");
  await screen.findByText("Folder /native");
  expect(screen.queryByLabelText("Folder or file path")).toBeNull();
  expect(screen.queryByLabelText("Project root")).toBeNull();
  await chooseView("Changes");
  await screen.findByText("Repository /native · Diff");
  await chooseView("Terminal");
  await screen.findByText("Terminal context project-one · /native");
  fireEvent.click(screen.getByRole("button", { name: "Another chat" }));
  await chooseView("Changes");
  await screen.findByText("Repository /other · Diff");
  await chooseView("Terminal");
  await screen.findByText("Terminal context project-two · /other");
  await chooseView("Files");
  await screen.findByText("Folder /other");
});

it("a chat without a Project does not pick another Project's folder", async () => {
  const { get } = setup("/threads/current", true, undefined, null);
  await chooseView("Files");
  await screen.findByText(
    "Open a chat in a project to see its files and changes.",
  );
  expect(screen.queryByText(/Folder \/unrelated/)).toBeNull();
  expect(
    get.mock.calls.some(([url]) => url === "/api/host/files/metadata"),
  ).toBe(false);
});

it.each(["directory", "file", "repository", "diff"])(
  "explicit projectless %s links retain their explorer without a folder chooser",
  async (target) => {
    setup(
      target === "directory" || target === "file"
        ? `/?native=files&native_path=${encodeURIComponent(target === "file" ? "/native/first.txt" : "/native")}`
        : `/?native=changes&native_path=%2Fnative${target === "diff" ? "&diff_path=first.txt&comparison=staged" : ""}`,
      true,
      target === "directory"
        ? Promise.resolve({ data: { kind: "directory" } })
        : undefined,
      null,
    );
    if (target === "file" || target === "diff") {
      await screen.findByText(
        target === "file" ? "Editor /native/first.txt" : "Patch first.txt",
      );
      fireEvent.click(
        screen.getByRole("button", { name: /Back to (files|changes)/ }),
      );
    }
    await screen.findByText(
      target === "directory" || target === "file"
        ? "Folder /native"
        : "Repository /native · Diff",
    );
    expect(screen.queryByLabelText("Folder or file path")).toBeNull();
  },
);

it.each([
  "/threads/current?terminal=terminal-foreign",
  "/?terminal=terminal-current",
])(
  "an unavailable terminal link never publishes terminal focus: %s",
  async (url) => {
    const { focus } = setup(url);
    await screen.findByText(/Terminal context/);
    expect(
      focus.mock.calls.some(([target]) => target?.kind === "terminal"),
    ).toBe(false);
  },
);

it.each(["/native", "C:\\native", "\\\\server\\share\\native"])(
  "folder navigation shows the current location, goes up, and returns to the Project root: %s",
  async (root) => {
    const first = joinPath(root, "nested");
    const second = joinPath(first, "nested");
    const { focus } = setup(
      "/threads/current",
      true,
      undefined,
      "project-one",
      "project-one",
      [root],
    );
    await chooseView("Files");
    await screen.findByText(`Folder ${root}`);
    expect(
      screen
        .getByRole("button", { name: "Up one level" })
        .hasAttribute("disabled"),
    ).toBe(true);
    fireEvent.click(screen.getByRole("button", { name: "Open child folder" }));
    await screen.findByText(`Folder ${first}`);
    fireEvent.click(screen.getByRole("button", { name: "Open child folder" }));
    await screen.findByText(`Folder ${second}`);
    const trail = screen.getByRole("navigation", { name: "Current folder" });
    expect(
      trail.querySelector('[aria-current="location"]')?.getAttribute("title"),
    ).toBe(second);
    fireEvent.click(screen.getByRole("button", { name: "Up one level" }));
    await screen.findByText(`Folder ${first}`);
    fireEvent.click(
      within(trail).getByRole("button", { name: "Current project" }),
    );
    await screen.findByText(`Folder ${root}`);
    expect(
      screen
        .getByRole("button", { name: "Up one level" })
        .hasAttribute("disabled"),
    ).toBe(true);
    await waitFor(() =>
      expect(focus).toHaveBeenLastCalledWith({ kind: "file", path: root }),
    );
  },
);

it.each(["Up", "breadcrumb", "working folder"])(
  "%s navigation from a file stays in its current tab even when the destination is already open",
  async (action) => {
    setup(
      "/threads/current?native=files&native_path=%2Fnative%2Fsrc%2Ffirst.txt",
      true,
      undefined,
      "project-one",
      "project-one",
      ["/native", "/other"],
    );
    await screen.findByText("Editor /native/src/first.txt");
    const fileTab = screen.getByRole("tab", { name: "first.txt" });
    const id = fileTab.id;
    fireEvent.click(screen.getByRole("button", { name: "New tab" }));
    fireEvent.click(screen.getByRole("button", { name: "Files" }));
    if (action === "working folder")
      fireEvent.click(
        within(
          screen.getByRole("navigation", { name: "Working folders" }),
        ).getByRole("button", { name: "other" }),
      );
    else fireEvent.click(screen.getByRole("button", { name: "Up one level" }));
    const destination = action === "working folder" ? "/other" : "/native";
    await screen.findByText(`Folder ${destination}`);
    const otherTab = screen.getByRole("tab", { name: "Files", selected: true });
    fireEvent.click(fileTab);
    if (action === "Up")
      fireEvent.click(screen.getByRole("button", { name: "Up one level" }));
    else if (action === "breadcrumb")
      fireEvent.click(
        within(
          screen.getByRole("navigation", { name: "Current folder" }),
        ).getByRole("button", { name: "native" }),
      );
    else
      fireEvent.click(
        within(
          screen.getByRole("navigation", { name: "Working folders" }),
        ).getByRole("button", { name: "other" }),
      );
    await screen.findByText(`Folder ${destination}`);
    expect(screen.getByRole("tab", { name: "Files", selected: true }).id).toBe(
      id,
    );
    expect(otherTab.getAttribute("aria-selected")).toBe("false");
    expect(screen.getAllByRole("tab")).toHaveLength(2);
    fireEvent.click(screen.getByRole("button", { name: "Open child folder" }));
    await screen.findByText(`Folder ${destination}/nested`);
    expect(screen.getByRole("tab", { name: "Files", selected: true }).id).toBe(
      id,
    );
    expect(screen.getAllByRole("tab")).toHaveLength(2);
  },
);

it("Back to files navigates within the current tab even when that folder is already open and retains its private buffer", async () => {
  const dirty = { dirty: true, text: "Keep this private edit" };
  const buffers = new Map([["/native/src/first.txt", dirty]]);
  setup(
    "/threads/current?native=files&native_path=%2Fnative%2Fsrc%2Ffirst.txt",
    true,
    undefined,
    "project-one",
    "project-one",
    ["/native"],
    <MessageText text="[Reopen file](/threads/current?native=files&native_path=%2Fnative%2Fsrc%2Ffirst.txt)" />,
    buffers,
  );
  await screen.findByText("Editor /native/src/first.txt");
  const fileTab = screen.getByRole("tab", { name: "first.txt · Unsaved" });
  const id = fileTab.id;
  const trail = screen.getByRole("navigation", { name: "Current folder" });
  expect(
    within(trail)
      .getByRole("button", { name: "src" })
      .getAttribute("aria-current"),
  ).toBe("location");
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  fireEvent.click(screen.getByRole("button", { name: "Files" }));
  const otherFolderTab = screen.getByRole("tab", {
    name: "Files",
    selected: true,
  });
  fireEvent.click(fileTab);
  fireEvent.click(screen.getByRole("button", { name: "Back to files" }));
  await screen.findByText("Folder /native/src");
  expect(screen.getByRole("tab", { name: "Files", selected: true }).id).toBe(
    id,
  );
  expect(otherFolderTab.getAttribute("aria-selected")).toBe("false");
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  expect(buffers.get("/native/src/first.txt")).toBe(dirty);
  fireEvent.click(screen.getByRole("link", { name: "Reopen file" }));
  await screen.findByText("Editor /native/src/first.txt");
  expect(
    screen.getByRole("tab", { name: "first.txt · Unsaved", selected: true }).id,
  ).toBe(id);
  expect(screen.getAllByRole("tab")).toHaveLength(2);
  expect(buffers.get("/native/src/first.txt")).toBe(dirty);
});

it("a failed parent open keeps the actual location and a late child response cannot undo successful Up navigation", async () => {
  const { get } = setup(
    "/threads/current?native=files&native_path=%2Fnative%2Fsrc",
  );
  await screen.findByText("Folder /native/src");
  get.mockRejectedValueOnce(new Error("Folder unavailable"));
  fireEvent.click(screen.getByRole("button", { name: "Up one level" }));
  await screen.findByText("Folder unavailable");
  expect(screen.getByText("Folder /native/src")).toBeTruthy();
  let finish!: (value: { data: { kind: string } }) => void;
  get.mockImplementationOnce(
    () =>
      new Promise((resolve) => {
        finish = resolve;
      }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Open child folder" }));
  await screen.findByText("Opening…");
  fireEvent.click(screen.getByRole("button", { name: "Up one level" }));
  await screen.findByText("Folder /native");
  finish({ data: { kind: "directory" } });
  await waitFor(() => expect(screen.queryByText("Opening…")).toBeNull());
  expect(screen.getByText("Folder /native")).toBeTruthy();
});

it("explicit external native links keep usable ancestry rather than an empty Project breadcrumb", async () => {
  setup("/threads/current?native=files&native_path=%2Fexternal%2Ffolder");
  await screen.findByText("Folder /external/folder");
  const trail = screen.getByRole("navigation", { name: "Current folder" });
  expect(
    within(trail).queryByRole("button", { name: "Current project" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Up one level" }));
  await screen.findByText("Folder /external");
  fireEvent.click(within(trail).getByRole("button", { name: "/" }));
  await screen.findByText("Folder /");
  expect(
    screen
      .getByRole("button", { name: "Up one level" })
      .hasAttribute("disabled"),
  ).toBe(true);
});

it("switching configured roots from a file view returns to the selected folder instead of leaving a stale editor", async () => {
  setup(
    "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt",
    true,
    undefined,
    "project-one",
    "project-one",
    ["/native", "/second-root"],
  );
  await screen.findByText("Editor /native/first.txt");
  fireEvent.click(
    within(
      screen.getByRole("navigation", { name: "Working folders" }),
    ).getByRole("button", { name: "second-root" }),
  );
  await screen.findByText("Folder /second-root");
  expect(screen.queryByText("Editor /native/first.txt")).toBeNull();
  expect(
    screen
      .getByRole("button", { name: "Up one level" })
      .hasAttribute("disabled"),
  ).toBe(true);
});

it("opens tool paths through existing private buffers without re-reading or replacing dirty text", async () => {
  const dirty = { dirty: true, text: "Unsaved private edit" };
  const buffers = new Map([["/native/deleted.txt", dirty]]);
  const { get } = setup(
    "/threads/current",
    true,
    undefined,
    "project-one",
    "project-one",
    ["/native"],
    <ToolCall
      tool={{
        id: "edit-one",
        name: "edit",
        input: { file_path: "/native/deleted.txt" },
        result: { ok: true },
      }}
    />,
    buffers,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Open on host" }));
  await screen.findByText("Editor /native/deleted.txt");
  expect(buffers.get("/native/deleted.txt")).toBe(dirty);
  expect(dirty.text).toBe("Unsaved private edit");
  expect(
    get.mock.calls.some(([url]) => url === "/api/host/files/metadata"),
  ).toBe(false);
  expect(screen.getByTestId("chat").textContent).toContain("/threads/current");
});

it("does not offer tool-to-host lookup when sharing is disabled", async () => {
  setup(
    "/threads/current",
    false,
    undefined,
    "project-one",
    "project-one",
    ["/native"],
    <ToolCall
      tool={{
        id: "edit-one",
        name: "edit",
        input: { file_path: "/native/a.txt" },
      }}
    />,
  );
  await screen.findByText("Edit");
  expect(screen.queryByRole("button", { name: "Open on host" })).toBeNull();
});

it.each(["/?project=project-one", "/new?project=project-one"])(
  "uses the selected local Project for Files, Changes and Terminal on %s",
  async (path) => {
    setup(path);
    await chooseView("Files");
    await screen.findByText("Folder /native");
    await chooseView("Changes");
    await screen.findByText("Repository /native · Diff");
    await chooseView("Terminal");
    await screen.findByText("Terminal context project-one · /native");
  },
);

it.each(["Message", "Shared prompt"])(
  "returns focus to the composer independently of its accessible name (%s)",
  async (label) => {
    function Content() {
      const returnToChat = useContext(ReturnToChat);
      return (
        <>
          <input aria-label={label} data-composer-editor="" />
          <button onClick={returnToChat}>Return to message</button>
        </>
      );
    }
    setup(
      "/threads/current?native=files",
      true,
      undefined,
      "project-one",
      "project-one",
      ["/native"],
      <Content />,
    );
    fireEvent.click(screen.getByRole("button", { name: "Return to message" }));
    await waitFor(() =>
      expect(document.activeElement).toBe(
        screen.getByRole("textbox", { name: label }),
      ),
    );
  },
);

it("terminal shortcuts toggle the panel, ignore repeat/modal/composition, and create with the current Project root", async () => {
  setup("/threads/current");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "New tab" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  const terminalVisible = () =>
    screen.queryByLabelText("Terminal panel")?.hasAttribute("hidden") === false;
  const chord = { code: "Backquote", key: "`", ctrlKey: true };
  fireEvent.keyDown(window, { ...chord, repeat: true });
  fireEvent.keyDown(window, { ...chord, isComposing: true });
  expect(terminalVisible()).toBe(false);
  fireEvent.keyDown(window, chord);
  expect(terminalVisible()).toBe(true);
  fireEvent.keyDown(window, { ...chord, repeat: true });
  expect(terminalVisible()).toBe(true);
  fireEvent.keyDown(window, chord);
  expect(terminalVisible()).toBe(false);
  const dialog = document.createElement("div");
  dialog.setAttribute("role", "dialog");
  document.body.append(dialog);
  fireEvent.keyDown(window, { ...chord, shiftKey: true });
  expect(screen.queryByText(/New terminal request/)).toBeNull();
  dialog.remove();
  fireEvent.keyDown(window, { ...chord, shiftKey: true });
  expect(terminalVisible()).toBe(true);
  expect(
    screen.getByText("New terminal request project-one · /native"),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Open peer resource" }));
  await screen.findByText(/Chat remains mounted \/settings/);
  expect(fireEvent.keyDown(window, chord)).toBe(true);
});

it.each([false, true])(
  "terminal creation shortcut respects sharing=%s and missing Project context",
  async (sharing) => {
    setup("/threads/current", sharing, undefined, null);
    await screen.findByText("Chat remains mounted /threads/current");
    if (sharing)
      await waitFor(() =>
        expect(
          screen
            .getByRole("button", { name: "New tab" })
            .hasAttribute("disabled"),
        ).toBe(false),
      );
    expect(
      fireEvent.keyDown(window, {
        code: "Backquote",
        ctrlKey: true,
        shiftKey: true,
      }),
    ).toBe(true);
    expect(screen.queryByText(/New terminal request/)).toBeNull();
    if (!sharing) {
      expect(
        fireEvent.keyDown(window, { code: "Backquote", ctrlKey: true }),
      ).toBe(true);
      expect(screen.queryByText(/Terminal context/)).toBeNull();
    }
  },
);

it("keeps Browser and Files in one resizable tabbed supporting pane", async () => {
  setup("/threads/current");
  await chooseView("Browser");
  const browserTab = screen.getByRole("tab", { name: "Browser" });
  expect(await screen.findByRole("tabpanel", { name: "Browser" })).toBeTruthy();
  expect(screen.queryByRole("tabpanel", { name: "Files" })).toBeNull();
  expect(
    screen.getByRole("separator", { name: "Resize supporting panel" }),
  ).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  const newTab = screen.getByRole("tab", { name: "New tab", selected: true });
  fireEvent.click(screen.getByRole("button", { name: "Browser" }));
  expect(screen.getAllByRole("tab", { name: "Browser" })).toHaveLength(1);
  expect(screen.getByRole("tab", { name: "Browser", selected: true })).toBe(
    browserTab,
  );
  expect(screen.getByRole("tab", { name: "New tab" })).toBe(newTab);
  fireEvent.click(newTab);
  expect(screen.getByRole("heading", { name: "Open a new tab" })).toBeTruthy();
  await chooseView("Files");
  expect(await screen.findByRole("tabpanel", { name: "Files" })).toBeTruthy();
  expect(screen.queryByRole("tabpanel", { name: "Browser" })).toBeNull();
});

it("opens new browser activity through the shared tab model in an empty split pane", async () => {
  setup("/threads/current");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "New tab" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  live.display.browserActivityRunId = "run-new";
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  const browser = await screen.findByRole("tab", { name: "Browser" });
  expect(browser.getAttribute("aria-selected")).toBe("true");
  expect(screen.getAllByRole("tab", { name: "Browser" })).toHaveLength(1);
});

it("browser activity suggests the existing Browser without replacing a new tab", async () => {
  setup("/threads/current");
  await chooseView("Browser");
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  expandSupportingPanel();
  live.display.browserActivityRunId = "run-new";
  fireEvent.click(screen.getByRole("tab", { name: "New tab" }));
  const suggestion = await screen.findByRole("button", {
    name: "View browser",
  });
  const newTabs = screen.getAllByRole("tab", { name: "New tab" }).length;
  expect(
    screen.getByRole("tab", { name: "Browser" }).getAttribute("aria-selected"),
  ).toBe("false");
  fireEvent.click(suggestion);
  expect(screen.getAllByRole("tab", { name: "Browser" })).toHaveLength(1);
  expect(
    screen.getByRole("tab", { name: "Browser" }).getAttribute("aria-selected"),
  ).toBe("true");
  expect(screen.getAllByRole("tab", { name: "New tab" })).toHaveLength(newTabs);
});

it("keeps Terminal in the shared pane across layout switches and retains its viewer when closing its tab", async () => {
  const { get } = setup("/threads/current?terminal=terminal-current");
  const terminalTab = await screen.findByRole("tab", { name: "Terminal" });
  const viewer = screen.getByLabelText("Terminal panel");
  const pane = screen.getByRole("tabpanel", { name: "Terminal" });
  expect(pane.contains(viewer)).toBe(true);
  expect(
    pane.parentElement?.contains(
      screen.getByRole("separator", { name: "Resize supporting panel" }),
    ),
  ).toBe(true);
  expect(
    screen.queryByRole("separator", { name: "Resize terminal" }),
  ).toBeNull();
  expandSupportingPanel();
  expect(screen.getByLabelText("Terminal panel")).toBe(viewer);
  expect(screen.getByRole("tab", { name: "Terminal" })).toBe(terminalTab);
  fireEvent.click(screen.getByRole("button", { name: "New tab" }));
  const newTab = screen.getByRole("tab", { name: "New tab" });
  fireEvent.click(screen.getByRole("button", { name: "Terminal" }));
  expect(screen.getByRole("tab", { name: "New tab" })).toBe(newTab);
  expect(screen.getAllByRole("tab", { name: "Terminal" })).toHaveLength(1);
  fireEvent.click(screen.getByRole("button", { name: "Close tab: Terminal" }));
  expect(viewer.hasAttribute("hidden")).toBe(true);
  await chooseView("Terminal");
  expect(screen.getByLabelText("Terminal panel")).toBe(viewer);
  expect(viewer.hasAttribute("hidden")).toBe(false);
  expect(
    get.mock.calls.filter(([url]) => url === "/api/host/terminals"),
  ).toHaveLength(0);
});

it("snapping the Terminal pane to full-width retains terminal focus and its session", async () => {
  const { focus } = setup("/threads/current?terminal=terminal-current");
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "terminal",
      terminal_id: "terminal-current",
    }),
  );
  for (let i = 0; i < 10; i++) {
    const splitter = screen.queryByRole("separator", {
      name: "Resize supporting panel",
    });
    if (!splitter) break;
    fireEvent.keyDown(splitter, { key: "ArrowLeft" });
  }
  expect(screen.getByRole("tab", { name: "Chat" })).toBeTruthy();
  expect(screen.getByRole("tabpanel", { name: "Terminal" })).toBeTruthy();
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "terminal",
      terminal_id: "terminal-current",
    }),
  );
});

it("the sidebar toggle maximizes Chat and restores the selected resource beside it", async () => {
  const { focus } = setup(
    "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt",
  );
  const editor = await screen.findByText("Editor /native/first.txt");
  expandSupportingPanel();
  const close = screen.getByRole("button", { name: "Close supporting panel" });
  expect(close.getAttribute("aria-expanded")).toBe("true");
  fireEvent.click(close);
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  expect(screen.queryByRole("tabpanel", { name: "first.txt" })).toBeNull();
  expect(screen.queryByRole("tab", { name: "Chat" })).toBeNull();
  const open = screen.getByRole("button", { name: "Open supporting panel" });
  expect(open.getAttribute("aria-expanded")).toBe("false");
  fireEvent.click(open);
  expect(screen.getByText("Editor /native/first.txt")).toBe(editor);
  expect(
    screen.getByRole("separator", { name: "Resize supporting panel" }),
  ).toBeTruthy();
  expandSupportingPanel();
  fireEvent.click(screen.getByRole("tab", { name: "Chat" }));
  fireEvent.click(
    screen.getByRole("button", { name: "Open supporting panel" }),
  );
  expect(
    screen.getByRole("separator", { name: "Resize supporting panel" }),
  ).toBeTruthy();
  expect(screen.getByText("Editor /native/first.txt")).toBe(editor);
});
