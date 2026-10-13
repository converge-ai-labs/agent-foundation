import { afterEach, expect, it, vi } from "vitest";
import {
  cropImage,
  loadImage,
  MAX_IMAGE_BYTES,
  MAX_SOURCE_BYTES,
  validateImageFile,
} from "./image";

afterEach(() => vi.unstubAllGlobals());

it("accepts source images above the former upload limit but bounds format and size", () => {
  for (const type of ["image/png", "image/jpeg", "image/webp"]) {
    expect(() =>
      validateImageFile({ type, size: MAX_SOURCE_BYTES } as File),
    ).not.toThrow();
  }
  expect(() =>
    validateImageFile({
      type: "image/png",
      size: MAX_SOURCE_BYTES + 1,
    } as File),
  ).toThrow("20 MiB");
  expect(() =>
    validateImageFile({ type: "image/svg+xml", size: 10 } as File),
  ).toThrow("PNG, JPEG, or WebP");
});

it("rejects corrupt and oversized decoded images before creating a canvas", async () => {
  let image: {
    naturalWidth: number;
    naturalHeight: number;
    onload: () => void;
    onerror: () => void;
  };
  vi.stubGlobal(
    "Image",
    class {
      naturalWidth = 8000;
      naturalHeight = 6000;
      onload = () => {};
      onerror = () => {};
      constructor() {
        image = this;
      }
    },
  );
  const oversized = loadImage("blob:large");
  image!.onload();
  await expect(oversized).rejects.toThrow("40 million pixels");
  const broken = loadImage("blob:corrupt");
  image!.onerror();
  await expect(broken).rejects.toThrow("could not be opened");
});

function encoder(blobs: (Blob | null)[]) {
  const drawImage = vi.fn();
  const toBlob = vi.fn((callback: (blob: Blob | null) => void) =>
    callback(blobs.shift() ?? null),
  );
  const context = { drawImage };
  const canvas = { width: 0, height: 0, getContext: () => context, toBlob };
  vi.stubGlobal("document", { createElement: () => canvas });
  return { canvas, drawImage, toBlob };
}
const image = { naturalWidth: 1600, naturalHeight: 1200 } as HTMLImageElement;
const crop = { x: 400, y: 100, width: 1000, height: 1000 };

it("exports the selected square at 512 pixels and lowers quality to meet the byte budget", async () => {
  const { canvas, drawImage, toBlob } = encoder([
    new Blob([new Uint8Array(MAX_IMAGE_BYTES + 1)], { type: "image/webp" }),
    new Blob(["compressed"], { type: "image/webp" }),
  ]);
  const file = await cropImage(image, crop);
  expect([canvas.width, canvas.height]).toEqual([512, 512]);
  expect(drawImage).toHaveBeenCalledWith(
    image,
    400,
    100,
    1000,
    1000,
    0,
    0,
    512,
    512,
  );
  expect(toBlob.mock.calls.map((call) => call.slice(1))).toEqual([
    ["image/webp", 0.85],
    ["image/webp", 0.7],
  ]);
  expect(file.type).toBe("image/webp");
  expect(file.name).toBe("avatar.webp");
  expect(file.size).toBeLessThanOrEqual(MAX_IMAGE_BYTES);
});

it("uses PNG fallback without flattening alpha, reduces dimensions, and never enlarges small crops", async () => {
  const { canvas, drawImage, toBlob } = encoder([
    new Blob([new Uint8Array(MAX_IMAGE_BYTES + 1)], { type: "image/png" }),
    new Blob(["transparent PNG"], { type: "image/png" }),
  ]);
  const file = await cropImage(image, crop);
  expect(file.type).toBe("image/png");
  expect(file.name).toBe("avatar.png");
  expect(canvas.width).toBe(256);
  expect(toBlob).toHaveBeenCalledTimes(2);
  expect(drawImage.mock.calls[1]?.slice(-2)).toEqual([256, 256]);
  const small = encoder([new Blob(["small"], { type: "image/webp" })]);
  await cropImage(image, { x: 0, y: 0, width: 40, height: 40 });
  expect([small.canvas.width, small.canvas.height]).toEqual([40, 40]);
});

it("refuses invalid crop coordinates and encoder failure instead of uploading original bytes", async () => {
  encoder([]);
  for (const invalid of [
    { ...crop, width: 0 },
    { ...crop, x: -1 },
    { ...crop, x: NaN },
    { ...crop, width: 200 },
    { ...crop, x: 900 },
  ])
    await expect(cropImage(image, invalid)).rejects.toThrow("Adjust the crop");
  await expect(cropImage(image, crop)).rejects.toThrow("could not be prepared");
});
