import { expect, it } from "vitest";
import {
  classifyAttachmentPreview,
  decodeUtf8Text,
  formatAttachmentSize,
  isRasterImageAttachment,
  TEXT_PREVIEW_LIMIT,
} from "./attachment-preview-policy";
import { documentLimit } from "../native/document-kind";
import { MAX_MEDIA_BYTES } from "../native/media-kind";

it("distinguishes raster images from SVG and non-image MIME types", () => {
  expect(isRasterImageAttachment("image/png")).toBe(true);
  expect(isRasterImageAttachment("image/jpeg")).toBe(true);
  expect(isRasterImageAttachment("image/webp")).toBe(true);
  expect(isRasterImageAttachment("image/svg+xml")).toBe(false);
  expect(isRasterImageAttachment("text/plain")).toBe(false);
  expect(isRasterImageAttachment(undefined)).toBe(false);
});

it("classifies unsupported archives before size or MIME checks", () => {
  expect(
    classifyAttachmentPreview({
      name: "bundle.zip",
      mediaType: "application/zip",
      size: 1024,
    }),
  ).toEqual({ kind: "unsupported" });
  expect(
    classifyAttachmentPreview({
      name: "archive.tar.gz",
      mediaType: "application/gzip",
      size: 99 * 1024 * 1024,
    }),
  ).toEqual({ kind: "unsupported" });
});

it("classifies documents within and above the 16 MiB preview limit", () => {
  expect(
    classifyAttachmentPreview({
      name: "spec.pdf",
      mediaType: "application/pdf",
      size: 2 * 1024 * 1024,
    }),
  ).toEqual({ kind: "document", documentKind: "pdf" });
  expect(
    classifyAttachmentPreview({
      name: "budget.xlsx",
      mediaType:
        "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
      size: 500_000,
    }),
  ).toEqual({ kind: "document", documentKind: "sheet" });
  expect(
    classifyAttachmentPreview({
      name: "deck.pptx",
      mediaType:
        "application/vnd.openxmlformats-officedocument.presentationml.presentation",
      size: documentLimit + 1,
    }),
  ).toEqual({
    kind: "too-large",
    category: "document",
    limitBytes: documentLimit,
  });
});

it("classifies audio and video within and above the 10 MiB limit while keeping TypeScript as text", () => {
  expect(
    classifyAttachmentPreview({
      name: "clip.mp3",
      mediaType: "audio/mpeg",
      size: 512_000,
    }),
  ).toEqual({ kind: "media", mediaKind: "audio" });
  expect(
    classifyAttachmentPreview({
      name: "demo.mp4",
      mediaType: "video/mp4",
      size: MAX_MEDIA_BYTES + 1,
    }),
  ).toEqual({
    kind: "too-large",
    category: "video",
    limitBytes: MAX_MEDIA_BYTES,
  });
  expect(
    classifyAttachmentPreview({
      name: "index.ts",
      mediaType: "video/mp2t",
      size: 2048,
    }),
  ).toEqual({ kind: "text", markdown: false, svg: false });
});

it("classifies text, Markdown, SVG, and oversized text attachments", () => {
  expect(
    classifyAttachmentPreview({
      name: "notes.txt",
      mediaType: "text/plain",
      size: TEXT_PREVIEW_LIMIT,
    }),
  ).toEqual({ kind: "text", markdown: false, svg: false });
  expect(
    classifyAttachmentPreview({
      name: "README.md",
      mediaType: "text/markdown",
      size: 1024,
    }),
  ).toEqual({ kind: "text", markdown: true, svg: false });
  expect(
    classifyAttachmentPreview({
      name: "diagram.svg",
      mediaType: "image/svg+xml",
      size: 4096,
    }),
  ).toEqual({ kind: "text", markdown: false, svg: true });
  expect(
    classifyAttachmentPreview({
      name: "huge.log",
      mediaType: "text/plain",
      size: TEXT_PREVIEW_LIMIT + 1,
    }),
  ).toEqual({
    kind: "too-large",
    category: "text",
    limitBytes: TEXT_PREVIEW_LIMIT,
  });
});

it("decodes valid UTF-8 and rejects invalid sequences or NUL bytes", () => {
  const valid = new TextEncoder().encode("Hello, 世界!\n").buffer;
  expect(decodeUtf8Text(valid)).toBe("Hello, 世界!\n");

  const withNul = new Uint8Array([0x41, 0x00, 0x42]).buffer;
  expect(decodeUtf8Text(withNul)).toBeNull();

  const invalidUtf8 = new Uint8Array([0xff, 0xfe, 0xfd]).buffer;
  expect(decodeUtf8Text(invalidUtf8)).toBeNull();
});

it("formats byte sizes into compact human-readable strings", () => {
  expect(formatAttachmentSize(512, "en-US")).toBe("512 B");
  expect(formatAttachmentSize(9784, "en-US")).toBe("9.6 KiB");
  expect(formatAttachmentSize(2 * 1024 * 1024, "en-US")).toBe("2 MiB");
});
