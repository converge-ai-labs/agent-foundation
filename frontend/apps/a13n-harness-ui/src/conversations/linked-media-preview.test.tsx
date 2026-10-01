// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { MessageText } from "./message-text";
import { OpenHostFile } from "./tool-call";
import { TransportContext } from "../transport/context";
import { ApiError, type Schema, type Transport } from "../transport/client";
import { MAX_MEDIA_BYTES } from "../native/media-kind";

afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
const link = (path: string) =>
  `/threads/current?native=files&native_path=${encodeURIComponent(path)}`;
function fixture(
  path = "/tmp/photo.png",
  size = 500_000,
  media_type = "image/png",
) {
  const file: Schema<"FileInfo"> = {
    entry: {
      path,
      kind: "file",
      size,
      revision: "reviewed",
      mode: 0,
      modified_ns: 0,
    },
    resolved_path: path,
    media_type,
  };
  const get = vi.fn(
    async (
      _url: string,
      options: { signal: AbortSignal; params: { query: { path: string } } },
    ) => ({
      data:
        options.params.query.path === path
          ? file
          : {
              ...file,
              entry: { ...file.entry, path: options.params.query.path },
              resolved_path: options.params.query.path,
              media_type: "text/plain",
            },
    }),
  );
  const fetch = vi.fn(
    async (_url: string, _options?: RequestInit) => new Response(null),
  );
  const post = vi.fn(async () => ({
    data: { url: "/api/host/files/transfer?token=reviewed", expires_at: 1000 },
  }));
  const create = vi.fn().mockReturnValue("blob:preview");
  const revoke = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = create;
      static revokeObjectURL = revoke;
    },
  );
  const transport = {
    client: { GET: get, POST: post },
    fetch,
  } as unknown as Transport;
  const open = vi.fn();
  const tree = (text = `[Original](${link(path)})`) => (
    <TransportContext value={transport}>
      <OpenHostFile value={open}>
        <MessageText text={text} />
      </OpenHostFile>
    </TransportContext>
  );
  return { tree, file, get, post, fetch, create, revoke, open };
}

it("uses reviewed streaming access for inline images and the expanded viewer without collecting Blobs", async () => {
  const f = fixture();
  const view = render(f.tree());
  const image = await screen.findByAltText("photo.png");
  Object.defineProperties(image, {
    naturalWidth: { value: 1408 },
    naturalHeight: { value: 768 },
  });
  fireEvent.load(image);
  expect(f.get).toHaveBeenCalledWith(
    "/api/host/files/info",
    expect.objectContaining({ params: { query: { path: "/tmp/photo.png" } } }),
  );
  expect(f.post).toHaveBeenCalledWith(
    "/api/host/files/transfers",
    expect.objectContaining({
      body: {
        path: "/tmp/photo.png",
        expected_revision: "reviewed",
        disposition: "inline",
      },
    }),
  );
  expect(image.getAttribute("src")).toBe(
    "/api/host/files/transfer?token=reviewed",
  );
  expect(view.container.querySelector("p figure")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Expand image: photo.png" }),
  );
  await screen.findByRole("dialog");
  expect(
    screen.getByRole("link", { name: "Download image" }).getAttribute("href"),
  ).toBe("/api/host/files/transfer?token=reviewed");
  fireEvent.click(screen.getByRole("button", { name: "Close image preview" }));
  fireEvent.click(screen.getByRole("link", { name: "Original" }));
  expect(f.open).toHaveBeenCalledWith("/tmp/photo.png");
  view.rerender(
    f.tree(`[Original](${link("/tmp/photo.png")})\n\nMore streamed text`),
  );
  expect(f.post).toHaveBeenCalledTimes(1);
  expect(f.fetch).not.toHaveBeenCalled();
  expect(f.create).not.toHaveBeenCalled();
  view.unmount();
  expect(image.hasAttribute("src")).toBe(false);
  expect(f.revoke).not.toHaveBeenCalled();
});

it.each(["https://external.test/full", link("/tmp/notes.txt")])(
  "renders a linked thumbnail without nested anchors and retains %s",
  async (href) => {
    const f = fixture();
    const view = render(
      f.tree(`[**_![Thumbnail](${link("/tmp/photo.png")})_**](${href})`),
    );
    await screen.findByAltText("photo.png");
    const original = screen.getByRole("link", { name: "Thumbnail" });
    expect(original.getAttribute("href")).toBe(href);
    expect(view.container.querySelector("a a, a figure, p figure")).toBeNull();
    expect(view.container.querySelectorAll("figure")).toHaveLength(1);
    if (href.startsWith("https://")) {
      expect(original.getAttribute("target")).toBe("_blank");
      expect(original.getAttribute("rel")).toBe("noopener noreferrer");
    } else {
      fireEvent.click(original);
      expect(f.open).toHaveBeenCalledWith("/tmp/notes.txt");
    }
  },
);

it("previews extensionless file links based on MIME without reading text", async () => {
  const f = fixture("/tmp/latest", 1234, "image/avif");
  render(f.tree());
  await screen.findByAltText("latest");
  expect(f.get).toHaveBeenCalledTimes(1);
  expect(f.get).toHaveBeenCalledWith("/api/host/files/info", expect.anything());
  expect(f.post).toHaveBeenCalledTimes(1);
});

it.each([
  "application/octet-stream",
  "image/svg+xml",
  "text/html",
  "application/pdf",
])(
  "keeps unrecognized %s files as original links without inline content requests",
  async (media_type) => {
    const f = fixture("/tmp/unknown", 1234, media_type);
    const view = render(f.tree());
    await waitFor(() =>
      expect(view.container.querySelector("figure")).toBeNull(),
    );
    expect(screen.getByRole("link", { name: "Original" })).toBeTruthy();
    expect(f.post).not.toHaveBeenCalled();
    expect(f.fetch).not.toHaveBeenCalled();
  },
);

it.each(["voice.wav", "clip.mp4"])(
  "renders %s with playback controls and an explicit decode fallback",
  async (name) => {
    vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
    vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
    const f = fixture(
      `/tmp/${name}`,
      15_047_567,
      name.endsWith("wav") ? "audio/x-wav" : "video/mp4",
    );
    const view = render(f.tree());
    const player = await screen.findByLabelText(
      `${name.endsWith("wav") ? "Audio" : "Video"} preview: ${name}`,
    );
    expect(player.getAttribute("src")).toBe(
      "/api/host/files/transfer?token=reviewed",
    );
    expect(f.post).toHaveBeenCalledWith(
      "/api/host/files/transfers",
      expect.objectContaining({
        body: {
          path: `/tmp/${name}`,
          expected_revision: "reviewed",
          disposition: "inline",
        },
      }),
    );
    expect(f.fetch).not.toHaveBeenCalled();
    expect(f.create).not.toHaveBeenCalled();
    expect(player.hasAttribute("controls")).toBe(true);
    expect(player.hasAttribute("autoplay")).toBe(false);
    fireEvent.error(player);
    await screen.findByText(/cannot be previewed/);
    expect(screen.getByRole("link", { name: "Original" })).toBeTruthy();
    view.unmount();
    expect(f.revoke).not.toHaveBeenCalled();
  },
);

it("keeps stale-revision errors visible and retries metadata before fetching again", async () => {
  const f = fixture();
  f.post.mockRejectedValueOnce(
    new ApiError("File content changed; refresh before selecting it.", 409),
  );
  render(f.tree());
  await screen.findByText(/File content changed/);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByAltText("photo.png");
  expect(f.get).toHaveBeenCalledTimes(2);
  expect(f.post).toHaveBeenCalledTimes(2);
});

it("reveals a native player's revision conflict and refreshes metadata before retrying", async () => {
  vi.spyOn(HTMLMediaElement.prototype, "pause").mockImplementation(() => {});
  vi.spyOn(HTMLMediaElement.prototype, "load").mockImplementation(() => {});
  const f = fixture("/tmp/clip.mp4", 15_047_567, "video/mp4");
  f.fetch.mockRejectedValueOnce(
    new ApiError("File content changed; refresh before playing.", 409),
  );
  render(f.tree());
  const player = await screen.findByLabelText("Video preview: clip.mp4");
  fireEvent.error(player);
  await screen.findByText(/File content changed/);
  expect(f.fetch).toHaveBeenCalledWith(
    "/api/host/files/transfer?token=reviewed",
    expect.objectContaining({ method: "HEAD" }),
  );
  f.file.entry.revision = "new";
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByLabelText("Video preview: clip.mp4");
  expect(f.get).toHaveBeenCalledTimes(2);
  expect(f.post).toHaveBeenLastCalledWith(
    "/api/host/files/transfers",
    expect.objectContaining({
      body: {
        path: "/tmp/clip.mp4",
        expected_revision: "new",
        disposition: "inline",
      },
    }),
  );
});

it("does not fetch oversized files and leaves the original link available", async () => {
  const f = fixture("/tmp/photo.png", MAX_MEDIA_BYTES + 1);
  render(f.tree());
  await screen.findByText(/Preview supports files up to 10 MiB/);
  expect(f.fetch).not.toHaveBeenCalled();
  expect(f.post).not.toHaveBeenCalled();
  expect(screen.getByRole("link", { name: "Original" })).toBeTruthy();
});

it("defers media reads until near the viewport and aborts an outstanding read on unmount", async () => {
  const f = fixture();
  let show!: () => void;
  const disconnect = vi.fn();
  vi.stubGlobal(
    "IntersectionObserver",
    class {
      constructor(callback: (entries: { isIntersecting: boolean }[]) => void) {
        show = () => callback([{ isIntersecting: true }]);
      }
      observe() {}
      disconnect = disconnect;
    },
  );
  let resolve!: (value: { data: Schema<"FileInfo"> }) => void;
  f.get.mockImplementationOnce(
    () =>
      new Promise((done) => {
        resolve = done;
      }),
  );
  const view = render(f.tree());
  expect(f.get).not.toHaveBeenCalled();
  show();
  await waitFor(() => expect(f.get).toHaveBeenCalledTimes(1));
  const signal = f.get.mock.calls[0][1].signal;
  view.unmount();
  expect(signal.aborted).toBe(true);
  resolve({ data: f.file });
  await Promise.resolve();
  expect(f.fetch).not.toHaveBeenCalled();
  expect(f.post).not.toHaveBeenCalled();
  expect(disconnect).toHaveBeenCalled();
});
