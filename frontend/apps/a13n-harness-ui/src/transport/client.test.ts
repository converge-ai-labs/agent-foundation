// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import { createTransport, NetworkError } from "./client";

afterEach(() => vi.unstubAllGlobals());

it("classifies browser network failures without depending on browser wording", async () => {
  const cause = new TypeError("Load failed");
  vi.stubGlobal("fetch", vi.fn().mockRejectedValue(cause));
  const transport = createTransport("", vi.fn());
  const error = await transport
    .fetch("/api/status")
    .catch((error: unknown) => error);
  expect(error).toBeInstanceOf(NetworkError);
  expect(error).toHaveProperty("cause", cause);
  expect(error).toHaveProperty(
    "message",
    "Unable to reach the server. Check your connection and try again.",
  );
  transport.close();
});

it("does not present cancellation as connection loss", async () => {
  const controller = new AbortController();
  const cause = new TypeError("Cancelled");
  vi.stubGlobal(
    "fetch",
    vi.fn(async () => {
      controller.abort();
      throw cause;
    }),
  );
  const transport = createTransport("", vi.fn());
  await expect(
    transport.fetch("/api/status", { signal: controller.signal }),
  ).rejects.toBe(cause);
  transport.close();
});

it("rewrites guest JSON and attachment writes with their body bytes intact", async () => {
  const requests: Request[] = [];
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      requests.push(request);
      return new Response("{}", {
        headers: { "Content-Type": "application/json" },
      });
    }),
  );
  const transport = createTransport("guest-key", vi.fn(), "share-one");
  await transport.client.POST("/api/threads/{thread_id}/submit", {
    params: { path: { thread_id: "thread-one" } },
    body: { parts: ["Hello"] },
  });
  expect(new URL(requests[0].url).pathname).toBe(
    "/api/shared/share-one/threads/thread-one/submit",
  );
  expect(await requests[0].json()).toEqual({ parts: ["Hello"] });
  await transport.fetch("/api/threads/thread-one/attachments", {
    method: "POST",
    body: new Uint8Array([1, 2, 3]),
  });
  expect(new Uint8Array(await requests[1].arrayBuffer())).toEqual(
    new Uint8Array([1, 2, 3]),
  );
  transport.close();
});
