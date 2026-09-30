// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { TransportContext } from "../transport/context";
import {
  ApiError,
  responseError,
  type Schema,
  type Transport,
} from "../transport/client";
import { FileBuffers, type FileBuffer } from "./buffer";
import { downloadFile } from "./file-transfer";
import { FileView } from "./file-view";

vi.mock("../configuration/editor", () => ({
  SourceEditor: ({ value, label }: { value: string; label: string }) => (
    <textarea aria-label={label} value={value} readOnly />
  ),
}));
vi.mock("./capture", () => ({
  CaptureContext: () => null,
  downloadBlob: vi.fn(),
}));
vi.mock("./file-transfer", async (actual) => ({
  ...(await actual<typeof import("./file-transfer")>()),
  downloadFile: vi.fn(),
}));
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

const source = "/api/host/files/transfer?token=reviewed";
function metadata(
  path = "/code/screen.png",
  size = 88_047,
  media_type = "image/png",
): Schema<"FileText"> & { media_type: string } {
  return {
    entry: {
      path,
      size,
      revision: "first",
      kind: "file",
      modified_ns: 0,
      mode: 0,
    },
    resolved_path: path,
    media_type,
    presentation: size > 512 * 1024 ? "too_large" : "binary",
    text: null,
  };
}
function fixture(initial = metadata()) {
  let current = initial;
  const get = vi.fn(async () => ({ data: current }));
  const post = vi.fn(
    async (
      _url: string,
      _options: {
        body: { path: string; expected_revision: string; disposition: string };
        signal?: AbortSignal;
      },
    ) => ({
      data: { url: source, expires_at: 1000 },
    }),
  );
  const fetch = vi.fn(
    async (_url: string, _init?: RequestInit) => new Response(null),
  );
  const create = vi.fn();
  const revoke = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    },
  );
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const buffers = new Map<string, FileBuffer>();
  const transport = {
    client: { GET: get, POST: post },
    fetch,
  } as unknown as Transport;
  const tree = () => (
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <FileBuffers value={buffers}>
          <FileView
            path={current.entry.path}
            open={vi.fn()}
            refresh={vi.fn()}
          />
        </FileBuffers>
      </TransportContext>
    </QueryClientProvider>
  );
  return {
    tree,
    get,
    post,
    fetch,
    create,
    revoke,
    update(next: Schema<"FileText"> & { media_type: string }) {
      current = next;
    },
  };
}
async function loaded(name = "screen.png") {
  const image = await screen.findByAltText(name);
  Object.defineProperties(image, {
    naturalWidth: { value: 1920 },
    naturalHeight: { value: 1080 },
  });
  fireEvent.load(image);
  await screen.findByText("1920 × 1080");
  return image;
}

it.each([
  ["screen.png", 88_047],
  ["photo.JPG", 600_000],
  ["photo.jpeg", 800],
  ["animated.gif", 900],
  ["screen.webp", 10 * 1024 * 1024],
])(
  "streams %s at %i bytes without collecting a Blob or treating it as editable text",
  async (name, size) => {
    const f = fixture(metadata(`/code/${name}`, size));
    const view = render(f.tree());
    const image = await loaded(name);
    expect(image.getAttribute("src")).toBe(source);
    expect(f.post).toHaveBeenCalledWith(
      "/api/host/files/transfers",
      expect.objectContaining({
        body: {
          path: `/code/${name}`,
          expected_revision: "first",
          disposition: "inline",
        },
      }),
    );
    expect(f.get).toHaveBeenCalledTimes(1);
    expect(f.get).toHaveBeenCalledWith(
      "/api/host/files/info",
      expect.anything(),
    );
    expect(screen.queryByRole("textbox")).toBeNull();
    expect(screen.queryByRole("button", { name: "Save" })).toBeNull();
    expect(f.fetch).not.toHaveBeenCalled();
    expect(f.create).not.toHaveBeenCalled();
    const signal = f.post.mock.calls[0]![1].signal;
    view.unmount();
    expect(signal?.aborted).toBe(true);
    expect(image.hasAttribute("src")).toBe(false);
    expect(f.revoke).not.toHaveBeenCalled();
  },
);

it("expands the streamed image in the shared viewer and streams the original download", async () => {
  const f = fixture();
  render(f.tree());
  await loaded();
  fireEvent.click(
    screen.getByRole("button", { name: "Expand image: screen.png" }),
  );
  const dialog = await screen.findByRole("dialog", { name: "screen.png" });
  fireEvent.click(within(dialog).getByRole("button", { name: "Actual size" }));
  expect(
    within(dialog).getByRole("button", { name: "Fit to screen" }),
  ).toBeTruthy();
  expect(
    within(dialog)
      .getByRole("link", { name: "Download image" })
      .getAttribute("href"),
  ).toBe(source);
  fireEvent.click(
    within(dialog).getByRole("button", { name: "Close image preview" }),
  );
  await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() => expect(downloadFile).toHaveBeenCalledWith(source));
  expect(f.post).toHaveBeenLastCalledWith(
    "/api/host/files/transfers",
    expect.objectContaining({
      body: {
        path: "/code/screen.png",
        expected_revision: "first",
        disposition: "attachment",
      },
    }),
  );
  expect(f.fetch).not.toHaveBeenCalled();
});

it("does not preview oversize images or unsupported binary formats but still allows downloads", async () => {
  const f = fixture(metadata("/code/huge.png", 10 * 1024 * 1024 + 1));
  const view = render(f.tree());
  await screen.findByText("Image exceeds the preview limit");
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Download" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  expect(f.post).not.toHaveBeenCalled();
  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() =>
    expect(f.post).toHaveBeenCalledWith(
      "/api/host/files/transfers",
      expect.objectContaining({
        body: {
          path: "/code/huge.png",
          expected_revision: "first",
          disposition: "attachment",
        },
      }),
    ),
  );
  f.update(metadata("/code/document.pdf", 88_047, "application/pdf"));
  view.rerender(f.tree());
  await screen.findByText("Binary file");
  const svg = metadata("/code/icon.svg", 88_047, "image/svg+xml");
  f.update({ ...svg, presentation: "text", text: "<svg />" });
  view.rerender(f.tree());
  await screen.findByRole("textbox");
  expect(screen.queryByRole("region", { name: "Image preview" })).toBeNull();
  expect(f.post).toHaveBeenCalledTimes(1);
  expect(f.fetch).not.toHaveBeenCalled();
});

it("streams videos above the attachment limit and releases native playback on close", async () => {
  const pause = vi
    .spyOn(HTMLMediaElement.prototype, "pause")
    .mockImplementation(() => {});
  const load = vi
    .spyOn(HTMLMediaElement.prototype, "load")
    .mockImplementation(() => {});
  const f = fixture(metadata("/code/clip.mp4", 15_047_567, "video/mp4"));
  const view = render(f.tree());
  const player = await screen.findByLabelText("Video preview: clip.mp4");
  expect(player.getAttribute("src")).toBe(source);
  expect(player.hasAttribute("controls")).toBe(true);
  expect(player.hasAttribute("autoplay")).toBe(false);
  expect(f.post).toHaveBeenCalledWith(
    "/api/host/files/transfers",
    expect.objectContaining({
      body: {
        path: "/code/clip.mp4",
        expected_revision: "first",
        disposition: "inline",
      },
    }),
  );
  expect(f.fetch).not.toHaveBeenCalled();
  expect(f.create).not.toHaveBeenCalled();
  fireEvent.loadedMetadata(player);
  expect(screen.queryByText("Loading video…")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Download" }));
  await waitFor(() => expect(f.post).toHaveBeenCalledTimes(2));
  expect(f.post.mock.calls[1]).toMatchObject([
    "/api/host/files/transfers",
    { body: { disposition: "attachment" } },
  ]);
  view.unmount();
  expect(pause).toHaveBeenCalled();
  expect(load).toHaveBeenCalled();
  expect(player.hasAttribute("src")).toBe(false);
});

it("previews an extensionless symlink using server MIME and its requested path", async () => {
  const f = fixture({
    ...metadata("/code/latest"),
    resolved_path: "/code/actual.png",
  });
  render(f.tree());
  await loaded("latest");
  expect(f.post).toHaveBeenCalledWith(
    "/api/host/files/transfers",
    expect.objectContaining({
      body: {
        path: "/code/latest",
        expected_revision: "first",
        disposition: "inline",
      },
    }),
  );
});

it("refreshes a changed revision and obtains new access after a decode failure", async () => {
  const f = fixture();
  f.post
    .mockResolvedValueOnce({
      data: { url: `${source}-first`, expires_at: 1000 },
    })
    .mockResolvedValueOnce({
      data: { url: `${source}-second`, expires_at: 1000 },
    })
    .mockResolvedValueOnce({
      data: { url: `${source}-retry`, expires_at: 1000 },
    });
  render(f.tree());
  const old = await loaded();
  f.update({
    ...metadata(),
    entry: { ...metadata().entry, revision: "second" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Refresh" }));
  await waitFor(() =>
    expect(screen.getByAltText("screen.png").getAttribute("src")).toBe(
      `${source}-second`,
    ),
  );
  expect(old.hasAttribute("src")).toBe(false);
  expect(f.post.mock.calls[1]![1].body.expected_revision).toBe("second");
  fireEvent.error(screen.getByAltText("screen.png"));
  await screen.findByText(/This image cannot be previewed/);
  expect(screen.getByRole("button", { name: "Download" })).toBeTruthy();
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await waitFor(() =>
    expect(screen.getByAltText("screen.png").getAttribute("src")).toBe(
      `${source}-retry`,
    ),
  );
  expect(f.post).toHaveBeenCalledTimes(3);
});

it("reports access revision conflicts and re-observes disk before retrying", async () => {
  const f = fixture();
  f.post.mockRejectedValueOnce(
    new ApiError(
      "File content changed; refresh before selecting or downloading.",
      409,
    ),
  );
  render(f.tree());
  await screen.findByText(/File content changed/);
  f.update({ ...metadata(), entry: { ...metadata().entry, revision: "new" } });
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await loaded();
  expect(f.post.mock.calls.at(-1)![1].body.expected_revision).toBe("new");
});

it.each([
  [409, /File content changed/],
  [403, /File access expired/],
])(
  "explains bodyless HEAD failures with status %i for native image loads",
  async (status, message) => {
    const f = fixture();
    f.fetch.mockRejectedValueOnce(
      await responseError(new Response(null, { status })),
    );
    render(f.tree());
    fireEvent.error(await screen.findByAltText("screen.png"));
    await screen.findByText(message);
    expect(f.fetch).toHaveBeenCalledWith(
      source,
      expect.objectContaining({ method: "HEAD" }),
    );
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await screen.findByAltText("screen.png");
    expect(f.post).toHaveBeenCalledTimes(2);
  },
);

it("aborts replaced access requests and ignores late signed URLs", async () => {
  const f = fixture();
  let resolve!: (value: { data: { url: string; expires_at: number } }) => void;
  f.post.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const view = render(f.tree());
  await waitFor(() => expect(resolve).toBeDefined());
  f.update(metadata("/code/next.png"));
  view.rerender(f.tree());
  await loaded("next.png");
  expect(f.post.mock.calls[0]![1].signal?.aborted).toBe(true);
  resolve({ data: { url: `${source}-late`, expires_at: 1000 } });
  await waitFor(() => expect(screen.queryByAltText("screen.png")).toBeNull());
  expect(screen.getByAltText("next.png").getAttribute("src")).toBe(source);
  expect(f.create).not.toHaveBeenCalled();
});
