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
