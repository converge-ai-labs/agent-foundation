// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import type Konva from "konva";
import {
  copyAnnotation,
  downloadAnnotation,
  exportAnnotation,
} from "./annotation-export";

afterEach(() => {
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.useRealTimers();
});

it("exports source-pixel crop from a disposable content clone", async () => {
  const blob = new Blob(["png"], { type: "image/png" });
  const toCanvas = vi.fn(() => ({
    toBlob: (done: BlobCallback) => done(blob),
  }));
  const destroy = vi.fn();
  const clone = vi.fn(() => ({ toCanvas, destroy }));
  const crop = { x: 21, y: 32, width: 640, height: 480 };
  expect(
    await exportAnnotation({ clone } as unknown as Konva.Group, crop),
  ).toBe(blob);
  expect(clone).toHaveBeenCalledWith({ x: 0, y: 0, scaleX: 1, scaleY: 1 });
  expect(toCanvas).toHaveBeenCalledWith({ ...crop, pixelRatio: 1 });
  expect(destroy).toHaveBeenCalledOnce();
});

it("releases the clone when rasterization or PNG encoding fails", async () => {
  const destroy = vi.fn();
  const toCanvas = vi.fn(() => {
    throw new Error("canvas failed");
  });
  const group = {
    clone: () => ({ toCanvas, destroy }),
  } as unknown as Konva.Group;
  const crop = { x: 0, y: 0, width: 20, height: 10 };
  expect(() => exportAnnotation(group, crop)).toThrow("canvas failed");
  expect(destroy).toHaveBeenCalledOnce();
  const empty = {
    clone: () => ({
      toCanvas: () => ({ toBlob: (done: BlobCallback) => done(null) }),
      destroy,
    }),
  } as unknown as Konva.Group;
  await expect(exportAnnotation(empty, crop)).rejects.toThrow("encode");
  expect(destroy).toHaveBeenCalledTimes(2);
});

it("submits a promise-backed PNG during the original copy gesture", async () => {
  let finish!: (blob: Blob) => void;
  const png = new Promise<Blob>((resolve) => {
    finish = resolve;
  });
  const write = vi.fn().mockResolvedValue(undefined);
  const item = vi.fn(function (this: object, data: object) {
    Object.assign(this, { data });
  });
  vi.stubGlobal("navigator", { clipboard: { write } });
  vi.stubGlobal("ClipboardItem", item);
  const copied = copyAnnotation(png);
  expect(write).toHaveBeenCalledOnce();
  expect(item).toHaveBeenCalledWith({ "image/png": png });
  finish(new Blob());
  await copied;
});

it("reports unsupported or rejected clipboard writes without consuming export errors", async () => {
  vi.stubGlobal("navigator", {});
  await expect(
    copyAnnotation(Promise.reject(new Error("encoding"))),
  ).rejects.toThrow("Download");
  vi.stubGlobal("ClipboardItem", function () {});
  vi.stubGlobal("navigator", {
    clipboard: { write: () => Promise.reject(new Error("denied")) },
  });
  await expect(copyAnnotation(Promise.resolve(new Blob()))).rejects.toThrow(
    "denied",
  );
});

it("downloads PNG and releases its object URL after navigation", async () => {
  vi.useFakeTimers();
  const createObjectURL = vi.fn(() => "blob:annotation");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal("URL", { createObjectURL, revokeObjectURL });
  const click = vi
    .spyOn(HTMLAnchorElement.prototype, "click")
    .mockImplementation(function (this: HTMLAnchorElement) {
      expect(this.download).toBe("browser-screenshot.png");
      expect(this.href).toBe("blob:annotation");
    });
  await downloadAnnotation(Promise.resolve(new Blob()));
  expect(click).toHaveBeenCalledOnce();
  expect(revokeObjectURL).not.toHaveBeenCalled();
  vi.runAllTimers();
  expect(revokeObjectURL).toHaveBeenCalledWith("blob:annotation");
});
