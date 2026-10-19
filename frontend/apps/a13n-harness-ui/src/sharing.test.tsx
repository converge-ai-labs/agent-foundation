// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTransport } from "./transport/client";
import { TransportContext } from "./transport/context";
import { SharePanel } from "./sharing";

afterEach(() => {
  cleanup();
  vi.unstubAllGlobals();
});

it("creates only on request, copies the issued link, saves permission and revokes", async () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
  const clipboard = vi.fn().mockResolvedValue(undefined);
  Object.defineProperty(navigator, "clipboard", {
    configurable: true,
    value: { writeText: clipboard },
  });
  const writes: { method: string; body?: unknown }[] = [];
  let grant = {
    share_id: "share-test",
    scope: "thread",
    target_id: "thread-one",
    permission: "view",
    created_at: "2026-10-19T12:00:00Z",
    expires_at: "2026-10-20T12:00:00Z",
  };
  let active = false;
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      const json = (value: unknown) =>
        new Response(JSON.stringify(value), {
          headers: { "Content-Type": "application/json" },
        });
      if (request.method !== "GET")
        writes.push({
          method: request.method,
          body:
            request.method === "DELETE"
              ? undefined
              : await request.clone().json(),
        });
      if (path === "/api/setup") return json({});
      if (path === "/api/projects") return json([]);
      if (request.method === "POST") {
        active = true;
        return json({ grant, key: "test-link-key" });
      }
      if (request.method === "PATCH") {
        grant = { ...grant, permission: (await request.json()).permission };
        return json(grant);
      }
      if (request.method === "DELETE") {
        active = false;
        return new Response(null, { status: 204 });
      }
      if (path === "/api/shares") return json(active ? [grant] : []);
      throw new Error(path);
    }),
  );
  const transport = createTransport("test-owner", vi.fn());
  const queries = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <SharePanel open onClose={() => {}} threadId="thread-one" />
      </TransportContext>
    </QueryClientProvider>,
  );
  await screen.findByText("Active links · 0");
  expect(writes).toEqual([]);
  fireEvent.click(screen.getByRole("button", { name: "Create link" }));
  const link = await screen.findByRole("textbox", { name: "Sharing link" });
  expect((link as HTMLInputElement).value).toContain(
    "/share/share-test#key=test-link-key",
  );
  expect(writes).toEqual([
    {
      method: "POST",
      body: {
        scope: "thread",
        target_id: "thread-one",
        permission: "view",
        lifetime_hours: 24,
      },
    },
  ]);
  fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(clipboard).toHaveBeenCalledWith((link as HTMLInputElement).value);
  fireEvent.click(screen.getByText("Active links · 1"));
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Permission" }));
  await user.click(await screen.findByRole("option", { name: "Can prompt" }));
  expect(writes).toHaveLength(1);
  await screen.findByText(/Save only for people you trust/);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(screen.getByText(/Can prompt · Expires/)).toBeTruthy(),
  );
  expect(writes[1]).toEqual({
    method: "PATCH",
    body: { permission: "prompt" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
  await screen.findByText("Active links · 0");
  expect(screen.queryByRole("textbox", { name: "Sharing link" })).toBeNull();
  expect(writes[2].method).toBe("DELETE");
  transport.close();
  queries.clear();
});
