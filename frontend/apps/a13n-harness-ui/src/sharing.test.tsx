// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";
import userEvent from "@testing-library/user-event";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { createTransport } from "./transport/client";
import { TransportContext } from "./transport/context";
import { SharePanel } from "./sharing";

const disposals: (() => void)[] = [];
afterEach(() => {
  cleanup();
  disposals.splice(0).forEach((dispose) => dispose());
  vi.unstubAllGlobals();
});

function renderSharing({ failCreate = false } = {}) {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
  const user = userEvent.setup();
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
      if (path === "/api/projects")
        return json([{ project_id: "project-one", name: "Design studio" }]);
      if (request.method === "POST") {
        if (failCreate) throw new TypeError("Connection interrupted");
        const body = await request.json();
        grant = {
          ...grant,
          scope: body.scope,
          target_id: body.target_id,
          permission: body.permission,
        };
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
  disposals.push(() => {
    transport.close();
    queries.clear();
  });
  render(
    <QueryClientProvider client={queries}>
      <TransportContext value={transport}>
        <SharePanel
          open
          onClose={() => {}}
          threadId="thread-one"
          projectId="project-one"
        />
      </TransportContext>
    </QueryClientProvider>,
  );
  return { writes, clipboard, user };
}

it("creates only on request, copies the issued link, saves permission and revokes", async () => {
  const { writes, clipboard, user } = renderSharing();
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
  expect(screen.queryByRole("combobox", { name: "Share" })).toBeNull();
  expect(
    screen.queryByRole("combobox", { name: "Link permission" }),
  ).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Copy link" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(clipboard).toHaveBeenCalledWith((link as HTMLInputElement).value);
  fireEvent.click(screen.getByText("Active links · 1"));
  await user.click(screen.getByRole("combobox", { name: "Permission" }));
  await user.click(await screen.findByRole("option", { name: "Can prompt" }));
  expect(writes).toHaveLength(1);
  await screen.findByText(/Save only for people you trust/);
  fireEvent.click(screen.getByRole("button", { name: "Save" }));
  await waitFor(() =>
    expect(
      within(screen.getByRole("region", { name: "Link sharing" })).getByText(
        "Can prompt",
      ),
    ).toBeTruthy(),
  );
  expect(writes[1]).toEqual({
    method: "PATCH",
    body: { permission: "prompt" },
  });
  fireEvent.click(screen.getByRole("button", { name: "Revoke" }));
  await screen.findByText("Active links · 0");
  expect(screen.queryByRole("textbox", { name: "Sharing link" })).toBeNull();
  expect(writes[2].method).toBe("DELETE");
  await user.click(screen.getByRole("button", { name: "Create link" }));
  await screen.findByRole("button", { name: "Copy link" });
  expect(screen.queryByRole("button", { name: "Copied" })).toBeNull();
  expect(writes).toHaveLength(4);
});

it("sets Project access and expiry, preserves the link on copy failure and resets for another link", async () => {
  const { writes, clipboard, user } = renderSharing();
  await screen.findByText("Active links · 0");
  await user.click(screen.getByRole("combobox", { name: "Share" }));
  await user.click(
    await screen.findByRole("option", {
      name: "Project · Design studio",
    }),
  );
  await user.click(screen.getByRole("combobox", { name: "Link permission" }));
  await user.click(await screen.findByRole("option", { name: "Can prompt" }));
  expect(
    screen.getByText(
      /prompts use the Agent’s tools and Environment permissions/,
    ),
  ).toBeTruthy();
  expect(screen.getByText(/Guests can also create Threads/)).toBeTruthy();
  await user.click(screen.getByRole("combobox", { name: "Expires in" }));
  await user.click(await screen.findByRole("option", { name: "7 days" }));
  expect(writes).toEqual([]);
  await user.click(screen.getByRole("button", { name: "Create link" }));
  const link = await screen.findByRole("textbox", { name: "Sharing link" });
  expect(writes).toEqual([
    {
      method: "POST",
      body: {
        scope: "project",
        target_id: "project-one",
        permission: "prompt",
        lifetime_hours: 168,
      },
    },
  ]);
  clipboard.mockRejectedValueOnce(new Error("Clipboard unavailable"));
  await user.click(screen.getByRole("button", { name: "Copy link" }));
  await screen.findByText(/copy it manually/);
  expect((link as HTMLInputElement).value).toContain("#key=test-link-key");
  await user.click(screen.getByRole("button", { name: "Copy link" }));
  await screen.findByRole("button", { name: "Copied" });
  expect(writes).toHaveLength(1);
  await user.click(screen.getByRole("button", { name: "Create another link" }));
  expect(screen.queryByRole("textbox", { name: "Sharing link" })).toBeNull();
  expect(screen.queryByText(/copy it manually/)).toBeNull();
  expect(screen.getByRole("combobox", { name: "Share" })).toBeTruthy();
  expect(writes).toHaveLength(1);
});

it("keeps grant management available after an unknown creation outcome without retrying", async () => {
  const { writes, user } = renderSharing({ failCreate: true });
  await screen.findByText("Active links · 0");
  await user.click(screen.getByRole("button", { name: "Create link" }));
  await screen.findByText(/the link may have been created/);
  expect(screen.getByText("Active links · 0")).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "Sharing link" })).toBeNull();
  expect(writes).toHaveLength(1);
});
