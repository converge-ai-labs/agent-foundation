import type { Transport } from "../transport/client";
import { attachmentPath } from "./attachment-thumbnail";
import {
  classifyAttachmentPreview,
  decodeUtf8Text,
  TEXT_PREVIEW_LIMIT,
} from "./attachment-preview-policy";

export const MAX_ATTACHMENT_CACHE_ENTRIES = 24;
export const MAX_ATTACHMENT_CACHE_BYTES = 16 * 1024 * 1024;

export type CachedAttachment = {
  blob: Blob;
  size: number;
  /** `undefined` = not yet decoded; `string` = valid NUL-free UTF-8; `null` = binary/invalid UTF-8. */
  text?: string | null;
};

type InflightEntry = {
  promise: Promise<CachedAttachment>;
  controller: AbortController;
  waiters: number;
};

class AttachmentCache {
  readonly entries = new Map<string, CachedAttachment>();
  readonly inflight = new Map<string, InflightEntry>();
  totalBytes = 0;

  get(key: string): CachedAttachment | undefined {
    const entry = this.entries.get(key);
    if (!entry) return undefined;
    // Refresh recency for LRU eviction.
    this.entries.delete(key);
    this.entries.set(key, entry);
    return entry;
  }

  set(key: string, entry: CachedAttachment) {
    if (entry.size > MAX_ATTACHMENT_CACHE_BYTES) return;
    const existing = this.entries.get(key);
    if (existing) {
      this.totalBytes -= existing.size;
      this.entries.delete(key);
    }
    this.entries.set(key, entry);
    this.totalBytes += entry.size;
    while (
      this.entries.size > MAX_ATTACHMENT_CACHE_ENTRIES ||
      this.totalBytes > MAX_ATTACHMENT_CACHE_BYTES
    ) {
      const oldest = this.entries.keys().next().value;
      if (oldest === undefined || oldest === key) break;
      const removed = this.entries.get(oldest);
      if (removed) this.totalBytes -= removed.size;
      this.entries.delete(oldest);
    }
  }
}

const caches = new WeakMap<Transport, AttachmentCache>();

function cacheFor(transport: Transport): AttachmentCache {
  let cache = caches.get(transport);
  if (!cache) {
    cache = new AttachmentCache();
    caches.set(transport, cache);
  }
  return cache;
}

function cacheKey(threadId: string, attachmentId: string): string {
  return `${threadId}\0${attachmentId}`;
}

function abortError(): Error {
  return new DOMException("The operation was aborted.", "AbortError");
}

function awaitWithSignal<T>(
  promise: Promise<T>,
  signal?: AbortSignal,
  onAbort?: () => void,
): Promise<T> {
  if (!signal) return promise;
  if (signal.aborted) {
    onAbort?.();
    return Promise.reject(signal.reason ?? abortError());
  }
  return new Promise<T>((resolve, reject) => {
    const handleAbort = () => {
      onAbort?.();
      reject(signal.reason ?? abortError());
    };
    signal.addEventListener("abort", handleAbort, { once: true });
    promise.then(
      (value) => {
        signal.removeEventListener("abort", handleAbort);
        resolve(value);
      },
      (error) => {
        signal.removeEventListener("abort", handleAbort);
        reject(error);
      },
    );
  });
}

async function ensureDecodedText(
  entry: CachedAttachment,
  signal?: AbortSignal,
): Promise<CachedAttachment> {
  if (entry.text !== undefined || entry.size > TEXT_PREVIEW_LIMIT) return entry;
  const bytes = await entry.blob.arrayBuffer();
  if (signal?.aborted) throw signal.reason ?? abortError();
  entry.text = decodeUtf8Text(bytes);
  return entry;
}

export function getCachedAttachment(
  transport: Transport,
  threadId: string,
  attachmentId: string,
): CachedAttachment | undefined {
  return cacheFor(transport).get(cacheKey(threadId, attachmentId));
}

export async function loadRetainedAttachment(
  transport: Transport,
  threadId: string,
  attachmentId: string,
  options?: { decodeText?: boolean; signal?: AbortSignal },
): Promise<CachedAttachment> {
  const signal = options?.signal;
  if (signal?.aborted) throw signal.reason ?? abortError();
  const cache = cacheFor(transport);
  const key = cacheKey(threadId, attachmentId);

  const cached = cache.get(key);
  if (cached) {
    return options?.decodeText ? ensureDecodedText(cached, signal) : cached;
  }

  let flight = cache.inflight.get(key);
  if (!flight) {
    const controller = new AbortController();
    const promise = transport
      .fetch(attachmentPath(threadId, attachmentId), {
        signal: controller.signal,
      })
      .then((response) => response.blob())
      .then((blob) => {
        const entry: CachedAttachment = { blob, size: blob.size };
        cache.set(key, entry);
        return entry;
      })
      .finally(() => {
        if (cache.inflight.get(key)?.controller === controller) {
          cache.inflight.delete(key);
        }
      });
    flight = { promise, controller, waiters: 0 };
    cache.inflight.set(key, flight);
  }

  const currentFlight = flight;
  currentFlight.waiters++;
  let released = false;
  const release = (abortIfLast: boolean) => {
    if (released) return;
    released = true;
    currentFlight.waiters--;
    if (abortIfLast && currentFlight.waiters <= 0) {
      currentFlight.controller.abort();
      if (cache.inflight.get(key) === currentFlight) {
        cache.inflight.delete(key);
      }
    }
  };

  try {
    const entry = await awaitWithSignal(currentFlight.promise, signal, () =>
      release(true),
    );
    release(false);
    return options?.decodeText ? ensureDecodedText(entry, signal) : entry;
  } catch (error) {
    release(false);
    throw error;
  }
}

export function prefetchRetainedAttachment(
  transport: Transport,
  threadId: string,
  attachment: {
    attachment_id: string;
    name: string;
    media_type?: string;
    size?: number;
  },
): void {
  if (attachment.size === undefined || attachment.size > TEXT_PREVIEW_LIMIT)
    return;
  const plan = classifyAttachmentPreview({
    name: attachment.name,
    mediaType: attachment.media_type,
    size: attachment.size,
  });
  if (plan.kind !== "text") return;
  const cache = cacheFor(transport);
  const key = cacheKey(threadId, attachment.attachment_id);
  if (cache.get(key)?.text !== undefined || cache.inflight.has(key)) return;
  void loadRetainedAttachment(transport, threadId, attachment.attachment_id, {
    decodeText: true,
  }).catch(() => {
    /* Quiet background prefetch; explicit open surfaces errors. */
  });
}
