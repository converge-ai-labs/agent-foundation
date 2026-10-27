// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { connectWindowPreview } from "./window-preview";

let socket: {
  onmessage?: (event: { data: Blob }) => void;
  onclose?: () => void;
  onerror?: () => void;
  close: ReturnType<typeof vi.fn>;
  send: ReturnType<typeof vi.fn>;
};
const decode = vi.fn();
const draw = vi.fn();
beforeEach(() => {
  vi.stubGlobal(
    "WebSocket",
    class {
      close = vi.fn();
      send = vi.fn();
      constructor() {
        socket = this;
      }
    },
  );
  vi.stubGlobal("createImageBitmap", decode);
  vi.spyOn(HTMLCanvasElement.prototype, "getContext").mockReturnValue({
    drawImage: draw,
  } as unknown as CanvasRenderingContext2D);
});
afterEach(() => {
  vi.unstubAllGlobals();
  vi.restoreAllMocks();
  vi.resetAllMocks();
});

it("decodes one frame at a time, drops intermediate frames, and never sends input", async () => {
  const canvas = document.createElement("canvas");
  const rendered = vi.fn();
  const failed = vi.fn();
  let resolve!: (image: ImageBitmap) => void;
  const first = new Promise<ImageBitmap>((done) => {
    resolve = done;
  });
  const image = {
    width: 800,
    height: 600,
    close: vi.fn(),
  } as unknown as ImageBitmap;
  decode.mockReturnValueOnce(first).mockResolvedValue(image);
  const stop = connectWindowPreview(
    "ws://localhost/preview",
    canvas,
    rendered,
    failed,
  );
  const frames = [
    new Blob(["first"]),
    new Blob(["dropped"]),
    new Blob(["latest"]),
  ];
  frames.forEach((data) => socket.onmessage!({ data }));
  expect(decode).toHaveBeenCalledTimes(1);
  resolve(image);
  await vi.waitFor(() => expect(rendered).toHaveBeenCalledTimes(2));
  expect(decode.mock.calls.map(([blob]) => blob)).toEqual([
    frames[0],
    frames[2],
  ]);
  expect([canvas.width, canvas.height]).toEqual([800, 600]);
  expect(image.close).toHaveBeenCalledTimes(2);
  expect(socket.send).not.toHaveBeenCalled();
  stop();
  expect([canvas.width, canvas.height]).toEqual([0, 0]);
  expect(failed).not.toHaveBeenCalled();
});

it.each(["close", "error", "dispose"])(
  "discards queued pixels and pending decode after %s",
  async (action) => {
    const canvas = document.createElement("canvas");
    const rendered = vi.fn();
    const failed = vi.fn();
    let resolve!: (image: ImageBitmap) => void;
    const pending = new Promise<ImageBitmap>((done) => {
      resolve = done;
    });
    const image = {
      width: 800,
      height: 600,
      close: vi.fn(),
    } as unknown as ImageBitmap;
    decode.mockReturnValue(pending);
    const stop = connectWindowPreview(
      "ws://localhost/preview",
      canvas,
      rendered,
      failed,
    );
    socket.onmessage!({ data: new Blob(["pending"]) });
    socket.onmessage!({ data: new Blob(["queued"]) });
    if (action === "dispose") stop();
    else if (action === "close") socket.onclose!();
    else socket.onerror!();
    resolve(image);
    await vi.waitFor(() => expect(image.close).toHaveBeenCalledOnce());
    expect(decode).toHaveBeenCalledTimes(1);
    expect(draw).not.toHaveBeenCalled();
    expect(rendered).not.toHaveBeenCalled();
    expect([canvas.width, canvas.height]).toEqual([0, 0]);
    expect(failed).toHaveBeenCalledTimes(action === "dispose" ? 0 : 1);
  },
);

it("clears the canvas and reports decode failure once", async () => {
  const canvas = document.createElement("canvas");
  const failed = vi.fn();
  decode.mockRejectedValue(new Error("Invalid JPEG"));
  connectWindowPreview("ws://localhost/preview", canvas, vi.fn(), failed);
  socket.onmessage!({ data: new Blob(["bad"]) });
  await vi.waitFor(() => expect(failed).toHaveBeenCalledOnce());
  socket.onclose!();
  expect(failed).toHaveBeenCalledOnce();
  expect([canvas.width, canvas.height]).toEqual([0, 0]);
});
