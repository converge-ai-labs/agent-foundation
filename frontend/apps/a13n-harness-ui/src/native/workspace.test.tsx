// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { useContext, useEffect, type ReactNode } from "react";
import { ReturnToChat } from "./capture";
import { ToolCall } from "../conversations/tool-call";
import { MessageText } from "../conversations/message-text";
import {
  cleanup,
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
  }: {
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
      <section hidden={!visible}>
        <p>
          Terminal context {projectId} · {directory}
        </p>
        <button onClick={() => select("")}>Close selected terminal</button>
      </section>
    );
  },
}));
afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
  localStorage.clear();
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
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
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
                          title: "Current conversation",
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
  return { focus, get };
}
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
  fireEvent.click(
    screen.getByRole("button", { name: /Back to (files|changes)/ }),
  );
  fireEvent.click(screen.getByRole("button", { name: "Open second file" }));
  await screen.findByText("Editor /native/second.txt");
  await waitFor(() =>
    expect(focus).toHaveBeenLastCalledWith({
      kind: "file",
      path: "/native/second.txt",
    }),
  );
  fireEvent.click(screen.getByRole("button", { name: "first.txt" }));
  await screen.findByText("Editor /native/first.txt");
  fireEvent.click(screen.getByRole("button", { name: "Close file explorer" }));
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
it("opens an assistant Host file link in the current conversation drawer", async () => {
  setup(
    "/threads/current",
    true,
    undefined,
    "project-one",
    "project-one",
    ["/native"],
    <MessageText text="[Open report](/threads/current?native=files&native_path=%2Fnative%2Freport.txt)" />,
  );
  fireEvent.click(await screen.findByRole("button", { name: "Files" }));
  await screen.findByText("Folder /native");
  fireEvent.click(screen.getByRole("button", { name: "Close file explorer" }));
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
  expect(screen.queryByText("Opening path…")).toBeNull();
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
  await screen.findByText("Patch reviewed.txt");
  expect(screen.getByLabelText("File explorer")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Terminal" })
      .getAttribute("aria-pressed"),
  ).toBe("false");
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
  fireEvent.click(screen.getByRole("button", { name: "Another conversation" }));
  finish({ data: { kind: "file" } });
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  expect(screen.queryByRole("button", { name: "slow.txt" })).toBeNull();
  expect(screen.getByTestId("chat").parentElement?.hidden).toBe(false);
});

it("retains open file tabs through Settings and closing the drawer returns focus to Chat", async () => {
  const { focus } = setup(
    "/threads/current?native=files&native_path=%2Fnative%2Ffirst.txt",
  );
  await screen.findByText("Editor /native/first.txt");
  fireEvent.click(screen.getByRole("button", { name: "Close file explorer" }));
  await waitFor(() => expect(focus).toHaveBeenLastCalledWith(null));
  expect(document.activeElement?.textContent).toContain("Chat remains mounted");
  fireEvent.click(screen.getByRole("button", { name: "Open peer resource" }));
  expect(screen.queryByLabelText("Open files")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Another conversation" }));
  fireEvent.click(screen.getByRole("button", { name: "Files" }));
  fireEvent.click(await screen.findByRole("button", { name: "first.txt" }));
  await screen.findByText("Editor /native/first.txt");
});

it("mobile Chat and conversation navigation reveal the conversation instead of leaving it behind Terminal", async () => {
  setup("/threads/current?terminal=terminal-current");
  await screen.findByRole("button", { name: "Terminal" });
  vi.stubGlobal("matchMedia", () => ({ matches: true }));
  fireEvent.click(screen.getByRole("button", { name: "Terminal" }));
  expect(
    screen
      .getByRole("button", { name: "Terminal" })
      .getAttribute("aria-pressed"),
  ).toBe("false");
  fireEvent.click(screen.getByRole("button", { name: "Terminal" }));
  fireEvent.click(screen.getByRole("button", { name: "Another conversation" }));
  expect(
    screen
      .getByRole("button", { name: "Terminal" })
      .getAttribute("aria-pressed"),
  ).toBe("false");
});

it.each(["Chat", "diff"])(
  "an explicit %s view supersedes a pending file open without a route change",
  async (target) => {
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
    if (target === "Chat")
      fireEvent.click(
        screen.getByRole("button", { name: "Close file explorer" }),
      );
    else {
      fireEvent.click(screen.getByRole("button", { name: "Changes" }));
      fireEvent.click(
        screen.getByRole("button", { name: "Select staged diff" }),
      );
    }
    finish({ data: { kind: "file" } });
    await waitFor(() =>
      expect(focus).toHaveBeenLastCalledWith(
        target === "Chat"
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
    if (target === "Chat")
      expect(screen.getByTestId("chat").parentElement?.hidden).toBe(false);
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
  await screen.findByText("Opening path…");
  reject(new Error("The file was deleted"));
  await screen.findByText("The file was deleted");
  expect(screen.getByText("Patch deleted.txt")).toBeTruthy();
  expect(
    screen
      .getByRole("button", { name: "Changes" })
      .getAttribute("aria-pressed"),
  ).toBe("true");
  expect(screen.queryByText("Opening path…")).toBeNull();
});

it("Files, Changes and new-terminal context follow Thread roots instead of changed Project roots or remembered folders", async () => {
  localStorage.setItem("a13n-harness-ui.native-directory", "/unrelated");
  setup("/threads/current", true, undefined, "project-one", "project-two");
  fireEvent.click(await screen.findByRole("button", { name: "Files" }));
  await screen.findByText("Folder /native");
  expect(screen.queryByLabelText("Folder or file path")).toBeNull();
  expect(screen.queryByLabelText("Project root")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Changes" }));
  await screen.findByText("Repository /native · Diff");
  fireEvent.click(screen.getByRole("button", { name: "Terminal" }));
  await screen.findByText("Terminal context project-one · /native");
  fireEvent.click(screen.getByRole("button", { name: "Another conversation" }));
  await screen.findByText("Repository /other · Diff");
  await screen.findByText("Terminal context project-two · /other");
  fireEvent.click(screen.getByRole("button", { name: "Files" }));
  await screen.findByText("Folder /other");
});

it("a conversation without a Project does not pick another Project's folder", async () => {
  const { get } = setup("/threads/current", true, undefined, null);
  fireEvent.click(await screen.findByRole("button", { name: "Files" }));
  await screen.findByText(
    "Open a conversation in a project to see its files and changes.",
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
        : target === "diff"
          ? "Repository /native · Diff first.txt"
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
    fireEvent.click(await screen.findByRole("button", { name: "Files" }));
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

it("file views retain folder navigation and a named return to the containing folder without losing the file tab", async () => {
  setup(
    "/threads/current?native=files&native_path=%2Fnative%2Fsrc%2Ffirst.txt",
  );
  await screen.findByText("Editor /native/src/first.txt");
  const trail = screen.getByRole("navigation", { name: "Current folder" });
  expect(
    within(trail)
      .getByRole("button", { name: "src" })
      .getAttribute("aria-current"),
  ).toBe("location");
  fireEvent.click(screen.getByRole("button", { name: "Back to files" }));
  await screen.findByText("Folder /native/src");
  fireEvent.click(screen.getByRole("button", { name: "first.txt" }));
  await screen.findByText("Editor /native/src/first.txt");
  fireEvent.click(
    within(trail).getByRole("button", { name: "Current project" }),
  );
  await screen.findByText("Folder /native");
  fireEvent.click(screen.getByRole("button", { name: "first.txt" }));
  await screen.findByText("Editor /native/src/first.txt");
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
  await screen.findByText("Opening path…");
  fireEvent.click(screen.getByRole("button", { name: "Up one level" }));
  await screen.findByText("Folder /native");
  finish({ data: { kind: "directory" } });
  await waitFor(() => expect(screen.queryByText("Opening path…")).toBeNull());
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

it.each([
  "/?project=project-one",
  "/new?project=project-one",
  "/new/thread_local?project=project-one",
])(
  "uses the selected local Project for Files, Changes and Terminal on %s",
  async (path) => {
    setup(path);
    fireEvent.click(await screen.findByRole("button", { name: "Files" }));
    await screen.findByText("Folder /native");
    fireEvent.click(screen.getByRole("button", { name: "Changes" }));
    await screen.findByText("Repository /native · Diff");
    fireEvent.click(screen.getByRole("button", { name: "Terminal" }));
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
