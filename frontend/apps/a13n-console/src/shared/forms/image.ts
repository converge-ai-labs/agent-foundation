/** Browser preparation for identity images; Service still owns upload admission. */
export const MAX_SOURCE_BYTES = 20 * 1024 * 1024;
export const MAX_SOURCE_PIXELS = 40_000_000;
export const MAX_IMAGE_BYTES = 256 * 1024;
export const MAX_IMAGE_DIMENSION = 512;
const TYPES = new Set(["image/png", "image/jpeg", "image/webp"]);

export type ImageCrop = { x: number; y: number; width: number; height: number };

export function validateImageFile(file: File) {
  if (!TYPES.has(file.type))
    throw new Error("Choose a PNG, JPEG, or WebP image.");
  if (file.size > MAX_SOURCE_BYTES)
    throw new Error("Choose an image smaller than 20 MiB.");
}

export function loadImage(url: string): Promise<HTMLImageElement> {
  return new Promise((resolve, reject) => {
    const image = new Image();
    image.onload = () => {
      if (!image.naturalWidth || !image.naturalHeight)
        reject(
          new Error("This image could not be opened. Choose another image."),
        );
      else if (image.naturalWidth * image.naturalHeight > MAX_SOURCE_PIXELS)
        reject(
          new Error("Choose an image with no more than 40 million pixels."),
        );
      else resolve(image);
    };
    image.onerror = () =>
      reject(
        new Error("This image could not be opened. Choose another image."),
      );
    image.src = url;
  });
}

function encode(canvas: HTMLCanvasElement, type: string, quality?: number) {
  return new Promise<Blob>((resolve, reject) => {
    canvas.toBlob(
      (blob) => {
        if (blob) resolve(blob);
        else
          reject(
            new Error("This image could not be prepared. Try another image."),
          );
      },
      type,
      quality,
    );
  });
}

/** Crop before encoding, never enlarge, and keep alpha even without a WebP encoder. */
export async function cropImage(
  image: HTMLImageElement,
  crop: ImageCrop,
): Promise<File> {
  if (
    !Object.values(crop).every(Number.isFinite) ||
    crop.width < 1 ||
    crop.height < 1 ||
    crop.x < 0 ||
    crop.y < 0 ||
    Math.abs(crop.width - crop.height) > 1 ||
    crop.x + crop.width > image.naturalWidth + 1 ||
    crop.y + crop.height > image.naturalHeight + 1
  )
    throw new Error("Adjust the crop before using this image.");
  const size = Math.min(
    MAX_IMAGE_DIMENSION,
    Math.floor(Math.min(crop.width, crop.height)),
  );
  const canvas = document.createElement("canvas");
  const context = canvas.getContext("2d");
  if (!context)
    throw new Error("This image could not be prepared. Try another image.");
  for (
    let dimension = size;
    dimension >= Math.min(size, 64);
    dimension = Math.floor(dimension / 2)
  ) {
    canvas.width = canvas.height = dimension;
    context.imageSmoothingEnabled = true;
    context.imageSmoothingQuality = "high";
    context.drawImage(
      image,
      crop.x,
      crop.y,
      crop.width,
      crop.height,
      0,
      0,
      dimension,
      dimension,
    );
    for (const quality of [0.85, 0.7, 0.5]) {
      const blob = await encode(canvas, "image/webp", quality);
      if (blob.size <= MAX_IMAGE_BYTES)
        return new File(
          [blob],
          blob.type === "image/webp" ? "avatar.webp" : "avatar.png",
          { type: blob.type },
        );
      // Unsupported WebP encoders return PNG; changing quality cannot shrink it.
      if (blob.type !== "image/webp") break;
    }
  }
  throw new Error(
    "This image could not be compressed enough. Choose another image.",
  );
}
