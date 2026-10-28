// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { TransportContext } from "../transport/context";
import { ApiError, type Schema, type Transport } from "../transport/client";
import { ComposerDrafts } from "../conversations/composer";
import { ThreadDraft, values } from "../conversations/draft";
import { CaptureContext } from "./capture";
import { FileBuffers, FileBuffer } from "./buffer";
import { FileView } from "./file-view";
import { NativeWorkspace } from "./workspace";
import { Changes } from "./changes";
import { FileOperation, Files } from "./files";

vi.mock("../configuration/editor", () => ({
  SourceEditor: ({
    value,
    onChange,
    label,
    readOnly,
  }: {
    value: string;
    onChange?: (value: string) => void;
    label: string;
    readOnly?: boolean;
  }) => (
    <textarea
      aria-label={label}
      value={value}
      readOnly={readOnly}
      onChange={(event) => onChange?.(event.target.value)}
    />
  ),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
const file: Schema<"FileText"> = {
  entry: {
    path: "/code/file.txt",
    revision: "first",
    kind: "file",
    size: 5,
    modified_ns: 0,
    mode: 0,
  },
  resolved_path: "/code/file.txt",
  presentation: "text",
  text: "first",
};
function fixture() {
  vi.stubGlobal("matchMedia", () => ({ matches: false }));
  const get = vi.fn(
    async (path: string, _options?: unknown): Promise<{ data: unknown }> => {
      if (path === "/api/status")
        return { data: { features: { host_files: true, host_git: true } } };
      if (path === "/api/projects") return { data: [] };
      return { data: file };
    },
  );
  const post = vi.fn();
  const put = vi.fn();
  const transport = {
    client: { GET: get, POST: post, PUT: put },
    fetch: vi.fn(),
  } as unknown as Transport;
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  const buffers = new Map<string, FileBuffer>();
  const a = new ThreadDraft(),
    b = new ThreadDraft();
  vi.spyOn(a, "synchronized", "get").mockReturnValue(true);
  vi.spyOn(b, "synchronized", "get").mockReturnValue(true);
  a.draftId = "draft-a";
  b.draftId = "draft-b";
  const drafts = new Map([
    ["thread-a", a],
    ["thread-b", b],
  ]);
  const Wrapper = ({ children }: { children: React.ReactNode }) => (
    <MemoryRouter>
      <QueryClientProvider client={queries}>
        <TransportContext.Provider value={transport}>
          <ComposerDrafts.Provider value={drafts}>
            <FileBuffers.Provider value={buffers}>
              {children}
            </FileBuffers.Provider>
          </ComposerDrafts.Provider>
        </TransportContext.Provider>
      </QueryClientProvider>
    </MemoryRouter>
  );
  return { get, post, put, transport, queries, buffers, a, b, Wrapper };
}
const attachment: Schema<"ThreadAttachment"> = {
  attachment_id: "attachment-native",
  name: "file.txt",
  size: 5,
  media_type: "text/plain",
  source: {
    path: "/code/file.txt",
    resolved_path: "/code/file.txt",
    revision: "first",
    location: "host",
  },
};
it("opens file actions by keyboard and preserves files when the editor is dismissed", async () => {
  const f = fixture();
  f.get.mockImplementation(async (path) => ({
    data:
      path === "/api/status"
        ? { features: { host_files: true, host_git: false } }
        : {
            entries: [file.entry],
            directory: { revision: "directory-one" },
            resolved_path: "/code",
            next_offset: null,
          },
  }));
  const user = userEvent.setup();
  render(<Files directory="/code" path="" open={vi.fn()} refresh={vi.fn()} />, {
    wrapper: f.Wrapper,
  });
  const actions = await screen.findByRole("button", {
    name: "Actions for file.txt",
  });
  actions.focus();
  await user.keyboard("{Enter}");
  await user.click(
    await screen.findByRole("menuitem", { name: "Rename or move" }),
  );
  await screen.findByRole("dialog", { name: "Rename or move" });
  expect(f.post).not.toHaveBeenCalled();
  await user.keyboard("{Escape}");
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  expect(
    screen.getByRole("button", { name: /^file\.txt\s*5 B$/ }),
  ).toBeTruthy();
  expect(f.post).not.toHaveBeenCalled();
  expect(f.put).not.toHaveBeenCalled();
});
it("adds only acknowledged attachment metadata to the original destination when navigation changes during capture", async () => {
  const f = fixture();
  let acknowledge!: (value: unknown) => void;
  f.post.mockImplementation(
    () =>
      new Promise((resolve) => {
        acknowledge = resolve;
      }),
  );
  f.a.doc.getText("text").insert(0, "My prompt");
  const view = render(
    <CaptureContext source={{ file }} threadId="thread-a" />,
    { wrapper: f.Wrapper },
  );
  fireEvent.click(screen.getByText("Add to message"));
  expect(values(f.a.doc).attachment_ids).toEqual([]);
  view.rerender(<CaptureContext source={{ file }} threadId="thread-b" />);
  acknowledge({
    data: { attachment, prompt_text: "must not be inserted twice" },
  });
  await waitFor(() =>
    expect(values(f.a.doc).attachment_ids).toEqual([attachment.attachment_id]),
  );
  expect(values(f.a.doc).prompt).toBe("My prompt");
  expect(values(f.b.doc).attachment_ids).toEqual([]);
  expect(f.post).toHaveBeenCalledWith(
    "/api/threads/{thread_id}/host-file-captures",
    expect.objectContaining({
      params: { path: { thread_id: "thread-a" } },
      body: { path: "/code/file.txt", expected_revision: "first" },
    }),
  );
});
it("a stale capture or replaced draft never silently selects context", async () => {
  const f = fixture();
  f.post.mockRejectedValue(new ApiError("Source changed; refresh.", 409));
  render(<CaptureContext source={{ file }} threadId="thread-a" />, {
    wrapper: f.Wrapper,
  });
  fireEvent.click(screen.getByText("Add to message"));
  await screen.findByText("Source changed; refresh.");
  expect(values(f.a.doc).attachment_ids).toEqual([]);
  f.post.mockImplementation(async () => {
    f.a.draftId = "replacement";
    return { data: { attachment } };
  });
  fireEvent.click(screen.getByText("Add to message"));
  await screen.findByText(/shared draft changed/);
  expect(values(f.a.doc).attachment_ids).toEqual([]);
});
it("range inputs validate before HTTP and preserve exact one-based endpoints", async () => {
  const f = fixture();
  f.post.mockResolvedValue({ data: { attachment } });
  render(<CaptureContext source={{ file }} threadId="thread-a" />, {
    wrapper: f.Wrapper,
  });
  fireEvent.click(screen.getByLabelText("Choose line range"));
  fireEvent.click(screen.getByText("Attach selection"));
  await screen.findByText(/Enter start and end line numbers/);
  expect(f.post).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("First line"), {
    target: { value: "2" },
  });
  fireEvent.click(screen.getByText("Attach selection"));
  await screen.findByText(/Enter start and end line numbers/);
  expect(f.post).not.toHaveBeenCalled();
  fireEvent.change(screen.getByLabelText("Last line (inclusive)"), {
    target: { value: "3" },
  });
  fireEvent.click(screen.getByText("Attach selection"));
  await waitFor(() =>
    expect(f.post).toHaveBeenCalledWith(
      expect.anything(),
      expect.objectContaining({
        body: expect.objectContaining({ start_line: 2, end_line: 3 }),
      }),
    ),
  );
});
it("dirty file text survives refresh, conflict and navigation until explicit resolution", async () => {
  const f = fixture();
  const props = { path: "/code/file.txt", refresh: vi.fn(), open: vi.fn() };
  const view = render(<FileView {...props} />, { wrapper: f.Wrapper });
  fireEvent.change(await screen.findByLabelText("File text: /code/file.txt"), {
    target: { value: "local changes" },
  });
  f.get.mockResolvedValue({
    data: {
      ...file,
      text: "external changes",
      entry: { ...file.entry, revision: "second" },
    },
  });
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await screen.findByText("Disk revision changed");
  expect(
    (screen.getByLabelText("File text: /code/file.txt") as HTMLTextAreaElement)
      .value,
  ).toBe("local changes");
  expect(
    (screen.getByRole("button", { name: "Save" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  view.unmount();
  render(<FileView {...props} />, { wrapper: f.Wrapper });
  await screen.findByText("Disk revision changed");
  expect(f.buffers.get(props.path)?.value).toBe("local changes");
  await waitFor(() =>
    expect(
      (screen.getByText("Keep local text") as HTMLButtonElement).disabled,
    ).toBe(false),
  );
  fireEvent.click(screen.getByText("Keep local text"));
  const confirmation = await screen.findByRole("dialog", {
    name: "Keep your local text?",
  });
  fireEvent.click(
    within(confirmation).getByRole("button", { name: "Keep local text" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  const retained = f.buffers.get(props.path)!;
  expect(retained.value).toBe("local changes");
  expect(retained.base.entry.revision).toBe("second");
  expect(retained.dirty).toBe(true);
  expect(f.put).not.toHaveBeenCalled();
  expect(screen.getByRole("status").textContent).toContain("Save explicitly");
  f.put.mockResolvedValue({ data: { ...file.entry, revision: "third" } });
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await screen.findByText("File saved.");
  expect(f.put).toHaveBeenCalledWith("/api/host/files/text", {
    body: {
      path: props.path,
      text: "local changes",
      expected_revision: "second",
    },
  });
});
it("binary and oversize views never fabricate editable text or inline context", async () => {
  const f = fixture();
  f.get.mockResolvedValue({
    data: { ...file, presentation: "binary", text: null },
  });
  render(
    <FileView path={file.entry.path} refresh={() => {}} open={() => {}} />,
    { wrapper: f.Wrapper },
  );
  await screen.findByText("Binary file");
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
  expect(screen.queryByLabelText("Choose line range")).toBeNull();
});
it("sharing off keeps the page usable and never attempts native reads", async () => {
  const f = fixture();
  f.get.mockImplementation(async (path) => ({
    data:
      path === "/api/status"
        ? { features: { host_files: false, host_git: false } }
        : [],
  }));
  render(
    <NativeWorkspace onFocus={vi.fn()} unauthorized={vi.fn()}>
      <p>Chat stays mounted</p>
    </NativeWorkspace>,
    { wrapper: f.Wrapper },
  );
  await waitFor(() =>
    expect(f.get.mock.calls.some(([path]) => path === "/api/status")).toBe(
      true,
    ),
  );
  expect(screen.queryByRole("button", { name: "Files" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Changes" })).toBeNull();
  expect(screen.queryByRole("button", { name: "Terminal" })).toBeNull();
  expect(screen.getByText("Chat stays mounted")).toBeTruthy();
  expect(f.get.mock.calls.some(([path]) => path.startsWith("/api/host"))).toBe(
    false,
  );
});
it("missing Git is an explicit unavailable view, not a clean tree", async () => {
  const f = fixture();
  f.get.mockImplementation(async () => ({
    data: { features: { host_files: true, host_git: false } },
  }));
  render(
    <Changes
      path="/code"
      selected={null}
      select={() => {}}
      openFile={() => {}}
    />,
    { wrapper: f.Wrapper },
  );
  await screen.findByText("Git unavailable");
  expect(screen.queryByText(/No staged entries/)).toBeNull();
  expect(f.get.mock.calls.some(([path]) => path.startsWith("/api/host"))).toBe(
    false,
  );
});

it("filters changed files, keeps pagination scoped, and exposes ignored files in the filter menu", async () => {
  const f = fixture();
  const select = vi.fn();
  const openFile = vi.fn();
  const repository = {
    root: "/code",
    git_dir: "/code/.git",
    common_dir: "/code/.git",
    head_oid: "head",
    branch: "main",
  };
  f.get.mockImplementation(async (path, ...args) => {
    const options = args[0] as
      | { params?: { query?: { include_ignored?: boolean; offset?: number } } }
      | undefined;
    const query = options?.params?.query;
    if (path === "/api/status")
      return { data: { features: { host_git: true } } };
    if (path === "/api/host/git/repository")
      return { data: { state: "repository", repository } };
    return {
      data: {
        repository,
        revision: "status-version",
        next_offset: query?.offset ? null : 1,
        entries: query?.offset
          ? []
          : query?.include_ignored
            ? [{ path: "cache.log", kind: "ignored" }]
            : [
                {
                  path: "checkout.ts",
                  kind: "ordinary",
                  index_status: ".",
                  worktree_status: "M",
                },
              ],
      },
    };
  });
  render(
    <Changes
      path="/code"
      selected={null}
      select={select}
      openFile={openFile}
    />,
    { wrapper: f.Wrapper },
  );
  fireEvent.click(
    await screen.findByRole("button", { name: /^checkout\.ts\s*M$/ }),
  );
  expect(select).toHaveBeenCalledWith(
    { repository_path: "/code", path: "checkout.ts", comparison: "unstaged" },
    [{ repository_path: "/code", path: "checkout.ts", comparison: "unstaged" }],
  );
  expect(screen.queryByText("Staged")).toBeNull();
  expect(
    screen.getByText("Counts and filtering cover loaded changes only."),
  ).toBeTruthy();
  fireEvent.change(screen.getByLabelText("Filter loaded changes by path"), {
    target: { value: "missing" },
  });
  expect(screen.getByText("No loaded changes match this filter.")).toBeTruthy();
  expect(screen.queryByText("No changes in this worktree.")).toBeNull();
  fireEvent.change(screen.getByLabelText("Filter loaded changes by path"), {
    target: { value: "" },
  });
  const user = userEvent.setup();
  await user.click(screen.getByRole("button", { name: "Change filters" }));
  await user.click(
    await screen.findByRole("menuitemcheckbox", {
      name: "Show ignored entries",
    }),
  );
  await user.click(
    await screen.findByRole("button", {
      name: /^cache\.log\s*Open in Files$/,
    }),
  );
  expect(openFile).toHaveBeenCalledWith("/code/cache.log");
  await user.click(screen.getByRole("button", { name: "Load more changes" }));
  await waitFor(() =>
    expect(
      screen.queryByRole("button", { name: "Load more changes" }),
    ).toBeNull(),
  );
});

it("nonstandard source separators offer only explicit whole-source capture, for Files and Changes", async () => {
  const f = fixture();
  f.post.mockResolvedValue({ data: { attachment } });
  const view = render(
    <CaptureContext
      source={{ file: { ...file, text: "first\fcontinued\nsecond\n" } }}
      threadId="thread-a"
      selection={{ start_line: 2, end_line: 2 }}
    />,
    { wrapper: f.Wrapper },
  );
  expect(screen.queryByLabelText("Choose line range")).toBeNull();
  expect(screen.queryByText("Attach selection")).toBeNull();
  expect(screen.getByText(/unusual line breaks/)).toBeTruthy();
  fireEvent.click(screen.getByText("Add to message"));
  await waitFor(() => expect(f.post).toHaveBeenCalled());
  expect(f.post.mock.calls[0][1].body.start_line).toBeUndefined();
  const diff: Schema<"GitDiff"> = {
    repository: {
      root: "/code",
      git_dir: "/code/.git",
      common_dir: "/code/.git",
      head_oid: "head",
      branch: "main",
    },
    path: "file.txt",
    comparison: "unstaged",
    index_revision: "index",
    revision: "diff",
    presentation: "text",
    text: "@@ -1 +1 @@\n+a\u2028b\n",
  };
  view.rerender(
    <CaptureContext
      source={{ diff }}
      threadId="thread-a"
      selection={{ start_line: 2, end_line: 2 }}
    />,
  );
  expect(screen.queryByLabelText("Choose line range")).toBeNull();
  expect(screen.queryByText("Attach selection")).toBeNull();
});

it.each(["/code", "C:\\code"])(
  "renames and moves within %s using the inspected revision",
  async (directory) => {
    const f = fixture();
    const sep = directory.startsWith("C:") ? "\\" : "/";
    const entry = { ...file.entry, path: `${directory}${sep}file.txt` };
    f.post.mockResolvedValue({ data: {} });
    const done = vi.fn();
    render(
      <FileOperation
        operation={{ kind: "move", entry }}
        directory={directory}
        refresh={vi.fn()}
        done={done}
      />,
      { wrapper: f.Wrapper },
    );
    const nameInput = screen.getByLabelText("Name") as HTMLInputElement;
    fireEvent.focus(nameInput);
    expect(nameInput.selectionStart).toBe(0);
    expect(nameInput.selectionEnd).toBe(4);
    const save = screen.getByRole("button", { name: "Save" });
    expect((save as HTMLButtonElement).disabled).toBe(true);
    fireEvent.change(screen.getByLabelText("Name"), {
      target: { value: "renamed.txt" },
    });
    fireEvent.click(screen.getByText("Folder · code"));
    fireEvent.change(screen.getByLabelText("Destination folder"), {
      target: { value: `${directory}${sep}archive` },
    });
    fireEvent.click(save);
    await waitFor(() =>
      expect(done).toHaveBeenCalledWith(
        `${directory}${sep}archive${sep}renamed.txt`,
      ),
    );
    expect(f.post).toHaveBeenCalledWith("/api/host/files/move", {
      body: {
        path: entry.path,
        destination: `${directory}${sep}archive${sep}renamed.txt`,
        expected_revision: "first",
      },
    });
  },
);

it("raw replacement uses the inspected revision and does not replay an unknown write", async () => {
  const f = fixture();
  const refresh = vi.fn(),
    done = vi.fn();
  vi.mocked(f.transport.fetch).mockRejectedValue(
    new TypeError("Response lost"),
  );
  render(
    <FileOperation
      operation={{ kind: "replace", entry: file.entry }}
      directory="/code"
      refresh={refresh}
      done={done}
    />,
    { wrapper: f.Wrapper },
  );
  fireEvent.change(screen.getByLabelText("Upload file (up to 10 MiB)"), {
    target: { files: [new File(["new bytes"], "new.bin")] },
  });
  fireEvent.click(screen.getByText("Replace file"));
  await screen.findByText("Response lost");
  expect(f.transport.fetch).toHaveBeenCalledWith(
    "/api/host/files/content?path=%2Fcode%2Ffile.txt&expected_revision=first",
    expect.objectContaining({ method: "PUT" }),
  );
  expect(refresh).toHaveBeenCalledOnce();
  expect(done).not.toHaveBeenCalled();
  fireEvent.click(screen.getByText("Replace file"));
  expect(f.transport.fetch).toHaveBeenCalledOnce();
});

it("adds the exact editor selection in one deliberate action without sending a message", async () => {
  const f = fixture();
  f.post.mockResolvedValue({ data: { attachment } });
  render(
    <CaptureContext
      source={{ file }}
      threadId="thread-a"
      selection={{ start_line: 2, end_line: 3 }}
    />,
    { wrapper: f.Wrapper },
  );
  expect(f.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Attach selection" }));
  await waitFor(() =>
    expect(values(f.a.doc).attachment_ids).toEqual([attachment.attachment_id]),
  );
  expect(f.post.mock.calls).toEqual([
    [
      "/api/threads/{thread_id}/host-file-captures",
      {
        params: { path: { thread_id: "thread-a" } },
        body: {
          path: "/code/file.txt",
          expected_revision: "first",
          start_line: 2,
          end_line: 3,
        },
      },
    ],
  ]);
});
