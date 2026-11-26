import {
  documentKind,
  documentLimit,
  unavailablePreview,
  type DocumentKind,
} from "../native/document-kind";
import {
  MAX_MEDIA_BYTES,
  mediaKind,
  type MediaKind,
} from "../native/media-kind";

export const TEXT_PREVIEW_LIMIT = 512 * 1024;

export type AttachmentPreviewPlan =
  | { kind: "unsupported" }
  | {
      kind: "too-large";
      category: "text" | "document" | "audio" | "video" | "image";
      limitBytes: number;
    }
  | { kind: "document"; documentKind: DocumentKind }
  | { kind: "media"; mediaKind: MediaKind }
  | { kind: "text"; markdown: boolean; svg: boolean };

export function isRasterImageAttachment(
  mediaType: string | undefined,
): boolean {
  return mediaKind(mediaType) === "image";
}

export function classifyAttachmentPreview(input: {
  name: string;
  mediaType?: string;
  size?: number;
}): AttachmentPreviewPlan {
  const { name, mediaType, size } = input;
  if (unavailablePreview(name)) return { kind: "unsupported" };
  const doc = documentKind(name);
  if (doc) {
    if (size !== undefined && size > documentLimit)
      return {
        kind: "too-large",
        category: "document",
        limitBytes: documentLimit,
      };
    return { kind: "document", documentKind: doc };
  }
  // .ts is both TypeScript and an MPEG transport stream suffix; let text decode decide.
  const media =
    /\.ts$/i.test(name) && mediaType === "video/mp2t"
      ? null
      : mediaKind(mediaType);
  if (media) {
    if (size !== undefined && size > MAX_MEDIA_BYTES)
      return {
        kind: "too-large",
        category: media,
        limitBytes: MAX_MEDIA_BYTES,
      };
    return { kind: "media", mediaKind: media };
  }
  if (size !== undefined && size > TEXT_PREVIEW_LIMIT)
    return {
      kind: "too-large",
      category: "text",
      limitBytes: TEXT_PREVIEW_LIMIT,
    };
  return {
    kind: "text",
    markdown: /\.(?:md|markdown)$/i.test(name),
    svg: mediaType === "image/svg+xml" || /\.svg$/i.test(name),
  };
}

export function decodeUtf8Text(bytes: ArrayBuffer): string | null {
  try {
    const decoded = new TextDecoder("utf-8", { fatal: true }).decode(bytes);
    return decoded.includes("\0") ? null : decoded;
  } catch {
    return null;
  }
}

export function formatAttachmentSize(size: number, locale?: string): string {
  if (!Number.isFinite(size) || size < 0) return "";
  if (size < 1024) return `${size.toLocaleString(locale)} B`;
  const units = ["KiB", "MiB", "GiB"];
  let value = size / 1024;
  let unit = 0;
  while (value >= 1024 && unit < units.length - 1) {
    value /= 1024;
    unit++;
  }
  return `${value.toLocaleString(locale, {
    minimumFractionDigits: 0,
    maximumFractionDigits: value >= 100 ? 0 : 1,
  })} ${units[unit]}`;
}
