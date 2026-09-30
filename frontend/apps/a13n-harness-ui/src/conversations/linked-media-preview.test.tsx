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
function fixture(path = "/tmp/photo.png", size = 500_000) {
  const file: Schema<"FileText"> = {
    entry: {
      path,
      kind: "file",
      size,
      revision: "reviewed",
      mode: 0,
      modified_ns: 0,
    },
    resolved_path: path,
    presentation: "binary",
    text: null,
  };
  const get = vi.fn(
    async (_url: string, _options: { signal: AbortSignal }) => ({ data: file }),
  );
  const fetch = vi.fn(async (_url: string, _options?: RequestInit) => ({
    blob: async () =>
      new Blob(["media bytes"], { type: "application/octet-stream" }),
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
  const transport = { client: { GET: get }, fetch } as unknown as Transport;
  const open = vi.fn();
  const tree = (text = `[Original](${link(path)})`) => (
    <TransportContext value={transport}>
      <OpenHostFile value={open}>
        <MessageText text={text} />
      </OpenHostFile>
    </TransportContext>
  );
  return { tree, file, get, fetch, create, revoke, open };
}

it("reads reviewed bytes for inline images, reuses the expanded viewer, and releases resources", async () => {
  const f = fixture();
  const view = render(f.tree());
  const image = await screen.findByAltText("photo.png");
  Object.defineProperties(image, {
    naturalWidth: { value: 1408 },
    naturalHeight: { value: 768 },
  });
  fireEvent.load(image);
  expect(f.get).toHaveBeenCalledWith(
    "/api/host/files/text",
    expect.objectContaining({ params: { query: { path: "/tmp/photo.png" } } }),
  );
  const url = new URL(f.fetch.mock.calls[0][0], "http://localhost");
  expect(url.searchParams.get("path")).toBe("/tmp/photo.png");
  expect(url.searchParams.get("expected_revision")).toBe("reviewed");
  expect(view.container.querySelector("p figure")).toBeNull();
  fireEvent.click(
    screen.getByRole("button", { name: "Expand image: photo.png" }),
  );
  await screen.findByRole("dialog");
  expect(
    screen.getByRole("link", { name: "Download image" }).getAttribute("href"),
  ).toBe("blob:preview");
  fireEvent.click(screen.getByRole("button", { name: "Close image preview" }));
  fireEvent.click(screen.getByRole("link", { name: "Original" }));
  expect(f.open).toHaveBeenCalledWith("/tmp/photo.png");
  view.rerender(
    f.tree(`[Original](${link("/tmp/photo.png")})\n\nMore streamed text`),
  );
  expect(f.fetch).toHaveBeenCalledTimes(1);
  view.unmount();
  expect(f.revoke).toHaveBeenCalledWith("blob:preview");
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

it.each(["voice.wav", "clip.mp4"])(
  "renders %s with playback controls and an explicit decode fallback",
  async (name) => {
    const f = fixture(`/tmp/${name}`);
    const view = render(f.tree());
    const player = await screen.findByLabelText(
      `${name.endsWith("wav") ? "Audio" : "Video"} preview: ${name}`,
    );
    expect(player.getAttribute("src")).toBe("blob:preview");
    expect(player.hasAttribute("controls")).toBe(true);
    expect(player.hasAttribute("autoplay")).toBe(false);
    fireEvent.error(player);
    await screen.findByText(/cannot be previewed/);
    expect(screen.getByRole("link", { name: "Original" })).toBeTruthy();
    view.unmount();
    expect(f.revoke).toHaveBeenCalledWith("blob:preview");
  },
);

it("keeps stale-revision errors visible and retries metadata before fetching again", async () => {
  const f = fixture();
  f.fetch.mockRejectedValueOnce(
    new ApiError("File content changed; refresh before selecting it.", 409),
  );
  render(f.tree());
  await screen.findByText(/File content changed/);
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByAltText("photo.png");
  expect(f.get).toHaveBeenCalledTimes(2);
  expect(f.fetch).toHaveBeenCalledTimes(2);
});

it("does not fetch oversized files and leaves the original link available", async () => {
  const f = fixture("/tmp/clip.mp4", MAX_MEDIA_BYTES + 1);
  render(f.tree());
  await screen.findByText(/Preview supports files up to 10 MiB/);
  expect(f.fetch).not.toHaveBeenCalled();
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
  let resolve!: (value: { data: Schema<"FileText"> }) => void;
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
  expect(disconnect).toHaveBeenCalled();
});
