import createClient from "openapi-fetch";
import { validators } from "./validators.generated.js";
import type { paths, components } from "./api.generated";

export type Model<K extends keyof components["schemas"]> =
  components["schemas"][K];
const KEY = "a13n-ui-api-key";
let key = "";
try {
  key = sessionStorage.getItem(KEY) ?? "";
} catch {
  /* Tab storage can be disabled. */
}
const fragment = new URLSearchParams(location.hash.slice(1));
if (location.hash) {
  history.replaceState(null, "", location.pathname + location.search);
  const values = fragment.getAll("api_key");
  if (values.length === 1 && values[0]) setKey(values[0]);
}
export function setKey(value: string) {
  key = value;
  try {
    if (key) sessionStorage.setItem(KEY, key);
    else sessionStorage.removeItem(KEY);
  } catch {
    /* Memory-only access still works. */
  }
}
export class ApiError extends Error {
  constructor(
    message: string,
    public readonly code: string,
  ) {
    super(message);
  }
}
export function decode<T>(path: string, value: unknown): T {
  const validate = validators[path];
  if (!validate || !validate(value))
    throw new ApiError(
      "Browser and server schemas do not match. Reload this page and restart the matching package.",
      "protocol_mismatch",
    );
  return value as T;
}
function authorization(): Record<string, string> {
  return key ? { Authorization: `Bearer ${key}` } : {};
}
function rejected() {
  setKey("");
  window.dispatchEvent(new Event("a13n-access-required"));
}
export const api = createClient<paths>({ baseUrl: "", cache: "no-store" });
api.use({
  onRequest({ request }) {
    if (key) request.headers.set("Authorization", `Bearer ${key}`);
    return request;
  },
  async onResponse({ response, schemaPath, request }) {
    if (response.status === 401) rejected();
    if (response.ok) {
      const pointer = schemaPath.replaceAll("~", "~0").replaceAll("/", "~1");
      decode(
        `#/paths/${pointer}/${request.method.toLowerCase()}/responses/200/content/application~1json/schema`,
        await response.clone().json(),
      );
    }
    return response;
  },
});
export function result<T>(value: {
  data?: T;
  error?: unknown;
  response: Response;
}): T {
  if (value.data !== undefined) return value.data;
  const error = value.error;
  if (typeof error === "object" && error !== null && "error" in error) {
    const detail = error.error;
    if (
      typeof detail === "object" &&
      detail !== null &&
      "message" in detail &&
      typeof detail.message === "string" &&
      "code" in detail &&
      typeof detail.code === "string"
    )
      throw new ApiError(detail.message, detail.code);
  }
  throw new ApiError(
    `Request failed (${value.response.status}).`,
    "request_failed",
  );
}
export function message(error: unknown): string {
  if (error instanceof ApiError) return error.message;
  return "Connection failed. If a command was sent, its outcome is unknown. Refresh authoritative state before deciding whether to retry; nothing is replayed automatically.";
}
export async function stream<T>(
  path: string,
  schemaPointer: string,
  signal: AbortSignal,
  receive: (frame: T) => void,
): Promise<void> {
  const response = await fetch(path, {
    headers: authorization(),
    signal,
    cache: "no-store",
  });
  if (response.status === 401) {
    rejected();
    throw new ApiError("Access key rejected.", "authentication_required");
  }
  if (!response.ok || !response.body) throw new Error("Stream unavailable");
  const reader = response.body.pipeThrough(new TextDecoderStream()).getReader();
  let buffer = "";
  try {
    while (!signal.aborted) {
      const next = await reader.read();
      if (next.done) throw new Error("Stream disconnected");
      buffer += next.value;
      if (buffer.length > 1024 * 1024)
        throw new ApiError(
          "Stream frame exceeds the protocol limit.",
          "protocol_mismatch",
        );
      let boundary: number;
      while ((boundary = buffer.indexOf("\n\n")) !== -1) {
        const frame = buffer.slice(0, boundary);
        buffer = buffer.slice(boundary + 2);
        const text = frame
          .split("\n")
          .filter((line) => line.startsWith("data: "))
          .map((line) => line.slice(6))
          .join("\n");
        if (text && !signal.aborted)
          receive(decode<T>(schemaPointer, JSON.parse(text)));
      }
    }
  } finally {
    await reader.cancel().catch(() => {});
    reader.releaseLock();
  }
}
