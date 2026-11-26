import { expect, it, vi } from "vitest";
import type { Transport } from "../transport/client";
import {
  getCachedAttachment,
  loadRetainedAttachment,
  MAX_ATTACHMENT_CACHE_ENTRIES,
  prefetchRetainedAttachment,
} from "./attachment-cache";

function blobResponse(text: string, size?: number): Response {
  const bytes = new TextEncoder().encode(text);
  return {
    blob: async () =>
      ({
        size: size ?? bytes.byteLength,
        type: "text/plain",
        arrayBuffer: async () =>
          bytes.buffer.slice(
            bytes.byteOffset,
            bytes.byteOffset + bytes.byteLength,
          ),
      }) as Blob,
  } as Response;
}

it("caches retained attachments per transport and deduplicates concurrent fetches", async () => {
  let resolveFetch!: (response: Response) => void;
  const pending = new Promise<Response>((resolve) => {
    resolveFetch = resolve;
  });
  const fetch = vi.fn().mockReturnValue(pending);
  const transport = { fetch } as unknown as Transport;

  const first = loadRetainedAttachment(transport, "t1", "a1", {
    decodeText: true,
  });
  const second = loadRetainedAttachment(transport, "t1", "a1", {
    decodeText: true,
  });
  expect(fetch).toHaveBeenCalledTimes(1);

  resolveFetch(blobResponse("cached content"));
  const [r1, r2] = await Promise.all([first, second]);
  expect(r1.text).toBe("cached content");
  expect(r2.text).toBe("cached content");
  expect(getCachedAttachment(transport, "t1", "a1")?.text).toBe(
    "cached content",
  );

  // Subsequent read hits cache with zero extra network call.
  const third = await loadRetainedAttachment(transport, "t1", "a1");
  expect(third.blob).toBe(r1.blob);
  expect(fetch).toHaveBeenCalledTimes(1);
});

it("evicts least-recently-used entries past the entry bound", async () => {
  const fetch = vi.fn(async (url: string) => blobResponse(`body:${url}`));
  const transport = { fetch } as unknown as Transport;

  for (let i = 0; i < MAX_ATTACHMENT_CACHE_ENTRIES + 2; i++) {
    await loadRetainedAttachment(transport, "t1", `att-${i}`);
  }
  expect(getCachedAttachment(transport, "t1", "att-0")).toBeUndefined();
  expect(getCachedAttachment(transport, "t1", "att-1")).toBeUndefined();
  expect(
    getCachedAttachment(
      transport,
      "t1",
      `att-${MAX_ATTACHMENT_CACHE_ENTRIES + 1}`,
    ),
  ).toBeTruthy();
});

it("prefetches only bounded text attachments and skips oversized, document, and archive files", async () => {
  const fetch = vi.fn(async () => blobResponse("small"));
  const transport = { fetch } as unknown as Transport;

  prefetchRetainedAttachment(transport, "t1", {
    attachment_id: "oversized",
    name: "big.log",
    media_type: "text/plain",
    size: 600 * 1024,
  });
  prefetchRetainedAttachment(transport, "t1", {
    attachment_id: "pdf-doc",
    name: "report.pdf",
    media_type: "application/pdf",
    size: 1024,
  });
  prefetchRetainedAttachment(transport, "t1", {
    attachment_id: "zip-file",
    name: "bundle.zip",
    media_type: "application/zip",
    size: 1024,
  });
  expect(fetch).not.toHaveBeenCalled();

  prefetchRetainedAttachment(transport, "t1", {
    attachment_id: "small-txt",
    name: "note.txt",
    media_type: "text/plain",
    size: 5,
  });
  expect(fetch).toHaveBeenCalledTimes(1);
  await vi.waitFor(() =>
    expect(getCachedAttachment(transport, "t1", "small-txt")?.text).toBe(
      "small",
    ),
  );
});
