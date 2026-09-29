import { requireSuccess } from "./errors.js";

export type Authentication =
  | { type: "session"; csrfToken?: string }
  | { type: "bearer"; token: string | (() => string | Promise<string>) };

export interface ClientOptions {
  /** Origin, optionally with a reverse-proxy prefix. */
  baseUrl: string;
  auth: Authentication;
  fetch?: typeof globalThis.fetch;
  maxReadRetries?: number;
}

/** A login session names the workspace of each business request; management routes take none. */
export function workspaceHeaders(workspaceId: string) {
  return { "X-Workspace-ID": workspaceId };
}

const publicMutations = new Set([
  "/api/v1/auth/bootstrap",
  "/api/v1/auth/login",
  "/api/v1/auth/password-reset",
  "/api/v1/auth/password-reset/confirm",
  "/api/v1/auth/email-change/confirm",
]);

export function delay(
  milliseconds: number,
  signal: AbortSignal,
): Promise<void> {
  signal.throwIfAborted();
  return new Promise((resolve, reject) => {
    const abort = () => {
      clearTimeout(timer);
      reject(signal.reason);
    };
    const timer = setTimeout(() => {
      signal.removeEventListener("abort", abort);
      resolve();
    }, milliseconds);
    signal.addEventListener("abort", abort, { once: true });
  });
}

export class Transport {
  readonly baseUrl: string;
  private readonly shutdown = new AbortController();
  private readonly fetcher: typeof globalThis.fetch;
  private csrfToken: string | undefined;
  private readonly retries: number;

  constructor(private readonly options: ClientOptions) {
    const url = new URL(options.baseUrl);
    if (
      !["http:", "https:"].includes(url.protocol) ||
      url.username ||
      url.password ||
      url.search ||
      url.hash
    ) {
      throw new TypeError(
        "baseUrl must be an HTTP(S) URL without credentials, query, or fragment.",
      );
    }
    this.baseUrl = url.href.replace(/\/$/, "");
    this.fetcher = options.fetch ?? globalThis.fetch.bind(globalThis);
    this.csrfToken =
      options.auth.type === "session" ? options.auth.csrfToken : undefined;
    this.retries = options.maxReadRetries ?? 2;
    if (!Number.isInteger(this.retries) || this.retries < 0 || this.retries > 5)
      throw new RangeError("maxReadRetries must be 0–5.");
  }

  httpOptions(baseUrl = this.baseUrl) {
    return {
      baseUrl,
      fetch: this.fetch,
      bodySerializer: (body: unknown) =>
        body instanceof Blob || body instanceof ReadableStream
          ? body
          : JSON.stringify(body),
    };
  }

  setCsrfToken(token: string | undefined): void {
    this.csrfToken = token;
  }
  get signal(): AbortSignal {
    return this.shutdown.signal;
  }
  close(): void {
    this.csrfToken = undefined;
    this.shutdown.abort();
  }

  fetch = async (input: Request): Promise<Response> => {
    const target = new URL(input.url);
    const base = new URL(this.baseUrl);
    if (
      target.origin !== base.origin ||
      !target.pathname.startsWith(`${base.pathname.replace(/\/$/, "")}/api/v1/`)
    ) {
      throw new TypeError("Requests must target the configured Service API.");
    }
    const signal = AbortSignal.any([input.signal, this.shutdown.signal]);
    signal.throwIfAborted();
    const headers = new Headers(input.headers);
    const auth = this.options.auth;
    const path = new URL(input.url).pathname.slice(
      new URL(this.baseUrl).pathname.replace(/\/$/, "").length,
    );
    const mutation = !["GET", "HEAD", "OPTIONS"].includes(input.method);
    if (auth.type === "bearer") {
      headers.set(
        "Authorization",
        `Bearer ${typeof auth.token === "function" ? await auth.token() : auth.token}`,
      );
    } else if (
      mutation &&
      !publicMutations.has(path) &&
      !/^\/api\/v1\/invitations\/[^/]+\/accept$/.test(path)
    ) {
      if (!this.csrfToken)
        throw new Error(
          "Restore the browser CSRF token before mutating Service resources.",
        );
      headers.set("X-CSRF-Token", this.csrfToken);
    }
    const request = new Request(input, {
      headers,
      signal,
      credentials: auth.type === "session" ? "same-origin" : "omit",
      redirect: "error",
    });
    const retries =
      request.method === "GET" || request.method === "HEAD" ? this.retries : 0;
    for (let attempt = 0; ; attempt++) {
      let response: Response;
      try {
        response = await this.fetcher(retries ? request.clone() : request);
      } catch (error) {
        if (signal.aborted || attempt >= retries) throw error;
        await delay(250 * 2 ** attempt, signal);
        continue;
      }
      if ([429, 502, 503, 504].includes(response.status) && attempt < retries) {
        const retryAfter = response.headers.get("Retry-After");
        const seconds = retryAfter === null ? NaN : Number(retryAfter);
        const milliseconds = Number.isFinite(seconds)
          ? seconds * 1000
          : retryAfter
            ? Date.parse(retryAfter) - Date.now()
            : 250 * 2 ** attempt;
        if (milliseconds <= 30_000 && milliseconds >= 0) {
          await response.body?.cancel();
          await delay(milliseconds, signal);
          continue;
        }
      }
      await requireSuccess(response);
      return response;
    }
  };
}
