import createClient from "openapi-fetch";
import { Realtime } from "./realtime";
import type { components, paths } from "../api.generated";

export type Schema<K extends keyof components["schemas"]> =
  components["schemas"][K];

export class NetworkError extends Error {
  constructor(cause: unknown) {
    super("Unable to reach the server. Check your connection and try again.", {
      cause,
    });
  }
}

export function isConnectionError(error: unknown): boolean {
  return (
    error instanceof NetworkError ||
    (error instanceof ApiError && [502, 503, 504].includes(error.status))
  );
}

export class ApiError extends Error {
  constructor(
    message: string,
    readonly status: number,
    readonly code?: string,
  ) {
    super(message);
  }
}
export async function responseError(response: Response): Promise<ApiError> {
  let message = `Request failed (${response.status}).`;
  let code: string | undefined;
  try {
    const body: unknown = await response.clone().json();
    if (typeof body === "object" && body !== null && "error" in body) {
      const error = body.error;
      if (typeof error === "object" && error !== null) {
        if ("message" in error && typeof error.message === "string")
          message = error.message;
        if ("code" in error && typeof error.code === "string")
          code = error.code;
      }
    }
  } catch {
    /* A proxy may return a non-JSON failure. */
  }
  if (response.status === 401)
    message = "Access expired. Enter the API key printed by this server.";
  return new ApiError(message, response.status, code);
}
export function createTransport(key: string, onUnauthorized: () => void) {
  // One lifetime covers queries, writes and response bodies, including open SSE streams.
  // Closing observation does not roll back a write the server may already have accepted.
  const lifetime = new AbortController();
  const realtime = new Realtime(key, () => {
    lifetime.abort();
    onUnauthorized();
  });
  lifetime.signal.addEventListener("abort", () => realtime.close(), {
    once: true,
  });
  const authenticatedFetch: typeof fetch = async (input, init) => {
    lifetime.signal.throwIfAborted();
    const request = new Request(
      input instanceof Request
        ? input
        : new URL(input.toString(), window.location.origin),
      init,
    );
    const headers = new Headers(request.headers);
    if (key) headers.set("Authorization", `Bearer ${key}`);
    const signal = AbortSignal.any([lifetime.signal, request.signal]);
    let response: Response;
    try {
      response = await fetch(
        new Request(request, { headers, cache: "no-store", signal }),
      );
    } catch (error) {
      if (!signal.aborted && error instanceof TypeError)
        throw new NetworkError(error);
      throw error;
    }
    lifetime.signal.throwIfAborted();
    if (!response.ok) {
      const error = await responseError(response);
      if (response.status === 401) {
        lifetime.abort();
        onUnauthorized();
      }
      throw error;
    }
    return response;
  };
  return {
    client: createClient<paths>({
      baseUrl: window.location.origin,
      fetch: authenticatedFetch,
    }),
    fetch: authenticatedFetch,
    realtime,
    key,
    close() {
      lifetime.abort();
    },
  };
}
export type Transport = ReturnType<typeof createTransport>;
export async function result<T>(request: Promise<{ data?: T }>): Promise<T> {
  const response = await request;
  if (response.data === undefined)
    throw new Error("Server returned an empty response.");
  return response.data;
}
