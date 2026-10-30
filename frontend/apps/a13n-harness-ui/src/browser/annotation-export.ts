import type Konva from "konva";
import type { Bounds } from "./annotation-model";

/** Export only the document group, never preview transforms or editing handles. */
export function exportAnnotation(
  group: Konva.Group,
  crop: Bounds,
): Promise<Blob> {
  const clone = group.clone({ x: 0, y: 0, scaleX: 1, scaleY: 1 });
  try {
    const canvas = clone.toCanvas({ ...crop, pixelRatio: 1 });
    return new Promise((resolve, reject) => {
      canvas.toBlob(
        (blob) =>
          blob
            ? resolve(blob)
            : reject(new Error("Could not encode the screenshot.")),
        "image/png",
      );
    });
  } finally {
    clone.destroy();
  }
}

/** Call directly in the copy gesture, before awaiting PNG encoding. */
export function copyAnnotation(png: Promise<Blob>): Promise<void> {
  // Handle encoding rejection even when ClipboardItem construction is unavailable.
  void png.catch(() => {});
  if (!navigator.clipboard?.write || typeof ClipboardItem === "undefined") {
    return Promise.reject(
      new Error("Image clipboard is unavailable. Download the PNG instead."),
    );
  }
  try {
    return navigator.clipboard.write([new ClipboardItem({ "image/png": png })]);
  } catch (error) {
    return Promise.reject(error);
  }
}

export async function downloadAnnotation(png: Promise<Blob>): Promise<void> {
  const url = URL.createObjectURL(await png);
  const link = document.createElement("a");
  link.href = url;
  link.download = "browser-screenshot.png";
  link.click();
  // Keep the URL alive until the browser has consumed the download navigation.
  setTimeout(() => URL.revokeObjectURL(url), 30_000);
}
