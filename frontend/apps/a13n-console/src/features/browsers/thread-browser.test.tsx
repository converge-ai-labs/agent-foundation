import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { act, cleanup, render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { createClient, type Client } from "../../service-client";
import type { Schema } from "../../shared/api";
import { conversationKeys } from "../conversations/api";
import {
  fixtureSession,
  fixtureThread,
} from "../conversations/transcript/fixture";
import { BrowserLifecycle, BrowsersPage } from "./page";
import { ThreadBrowserDialog } from "./thread-browser";

let client: Client;
let cache: QueryClient;
let verbs: string[];
let thread: Schema["ThreadView"];
let session: Schema["SessionView"];
let browser: Schema["Browser"];
let options: Schema["Browser"][];
let catalogFails: boolean;
let refuse: boolean;
let requests: Request[];
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws" },
    basePath: "/workspace/ws",
    can: (verb: string) => verbs.includes(verb),
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string, values?: { seconds?: number }) =>
      values?.seconds === undefined
        ? key
        : key.replace("{{seconds}}", String(values.seconds)),
    i18n: { resolvedLanguage: "en" },
  }),
}));
vi.mock("./viewer", () => ({ BrowserViewer: () => <div>Mounted viewer</div> }));
beforeEach(() => {
  verbs = ["read", "run", "write"];
  thread = fixtureThread({ version: 1 });
  session = fixtureSession();
  browser = {
    id: "browser_1",
    name: "Retained browser",
    version: 3,
    status: "ready",
    control: "automation",
    source: "managed",
    generation: 1,
    episode: 0,
  } as Schema["Browser"];
  options = [browser];
  catalogFails = false;
  refuse = false;
  requests = [];
  cache = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  client = createClient({
    baseUrl: "https://service.test",
    auth: { type: "session", csrfToken: "test-csrf" },
    maxReadRetries: 0,
    fetch: async (input, init) => {
      const request = new Request(input, init);
      const path = new URL(request.url).pathname;
      if (request.method !== "GET") {
        requests.push(request);
        if (refuse)
          return Response.json(
            { error: { code: "precondition_failed", message: "Changed" } },
            { status: 412 },
          );
        return Response.json(path.endsWith("/browser") ? thread : browser);
      }
      if (path.endsWith("/threads/thr_1/browser"))
        return Response.json({
          browser_id: browser.id,
          tab_access: "thread",
          preparation: "first_use",
        });
      if (path.endsWith("/threads/thr_1")) return Response.json(thread);
      if (path.endsWith("/sessions/ses_1")) return Response.json(session);
      if (path.endsWith("/browsers/browser_1")) return Response.json(browser);
      if (path.endsWith("/browsers"))
        return catalogFails
          ? Response.json(
              {
                error: { code: "unavailable", message: "Catalog unavailable" },
              },
              { status: 503 },
            )
          : Response.json({ items: options, next_cursor: null });
      throw new Error(`Unexpected request ${request.method} ${path}`);
    },
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
});
function start(lifecycle = false) {
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        {lifecycle ? (
          <BrowserLifecycle browser={browser} />
        ) : (
          <ThreadBrowserDialog thread={thread} onClose={() => {}} />
        )}
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
it.each(["viewer", "application", "child", "archived"])(
  "withholds Thread mutation and control for %s",
  async (kind) => {
    if (kind === "viewer") verbs = ["read"];
    if (kind === "application") session = fixtureSession({ labels: {} });
    if (kind === "child") thread = fixtureThread({ origin: "child" });
    if (kind === "archived")
      thread = fixtureThread({ archived_at: "2026-10-31T00:00:00Z" });
    start();
    await screen.findByText("Retained browser");
    expect(screen.queryByRole("button", { name: "Select browser" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Take control" })).toBeNull();
    expect(screen.queryByRole("button", { name: "Stop browser" })).toBeNull();
    expect(requests).toEqual([]);
  },
);
it("keeps the edit's Thread version through refresh and explicitly reloads after refusal", async () => {
  const user = userEvent.setup();
  start();
  await user.click(
    await screen.findByRole("button", { name: "Select browser" }),
  );
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Save selection",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  thread = fixtureThread({ version: 2 });
  act(() =>
    cache.setQueryData(conversationKeys("ws").thread(thread.id), thread),
  );
  refuse = true;
  await user.click(screen.getByRole("button", { name: "Save selection" }));
  await screen.findByRole("button", { name: "Reload" });
  expect(requests[0].headers.get("If-Match")).toBe('"thr_1:1"');
  expect(await requests[0].clone().json()).toEqual({
    browser_id: "browser_1",
    tab_access: "thread",
    preparation: "first_use",
  });
  await user.click(screen.getByRole("button", { name: "Reload" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Reload" })).toBeNull(),
  );
  refuse = false;
  await user.click(screen.getByRole("button", { name: "Save selection" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1].headers.get("If-Match")).toBe('"thr_1:2"');
});
it("does not pair a cached binding with a newer streamed Thread version before editing", async () => {
  const user = userEvent.setup();
  start();
  await screen.findByText("Retained browser");
  // Another client changed the binding; only its Thread update reached this client.
  thread = fixtureThread({ version: 2 });
  act(() =>
    cache.setQueryData(conversationKeys("ws").thread(thread.id), thread),
  );
  await user.click(screen.getByRole("button", { name: "Select browser" }));
  await waitFor(() =>
    expect(
      (
        screen.getByRole("button", {
          name: "Save selection",
        }) as HTMLButtonElement
      ).disabled,
    ).toBe(false),
  );
  refuse = true;
  await user.click(screen.getByRole("button", { name: "Save selection" }));
  await screen.findByRole("button", { name: "Reload" });
  expect(requests[0].headers.get("If-Match")).toBe('\"thr_1:1\"');
  expect(await requests[0].clone().json()).toMatchObject({
    browser_id: "browser_1",
  });
});
it.each(["missing", "failed"])(
  "retains a saved selection when the browser catalog is %s",
  async (kind) => {
    options = [];
    catalogFails = kind === "failed";
    start();
    await userEvent.click(
      await screen.findByRole("button", { name: "Select browser" }),
    );
    await screen.findByText("browser_1");
    await waitFor(() =>
      expect(
        (
          screen.getByRole("button", {
            name: "Save selection",
          }) as HTMLButtonElement
        ).disabled,
      ).toBe(true),
    );
    expect(requests).toEqual([]);
  },
);
it("uses the observed Browser version for takeover and handback and disconnects the viewer", async () => {
  const user = userEvent.setup();
  start();
  await user.click(await screen.findByRole("button", { name: "Take control" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].headers.get("If-Match")).toBe('"browser_1:3"');
  expect(await requests[0].clone().json()).toEqual({ thread_id: "thr_1" });
  browser = { ...browser, control: "human", episode: 1, version: 4 };
  act(() =>
    cache.setQueryData(["browsers", "ws", "detail", browser.id], browser),
  );
  await user.click(await screen.findByRole("button", { name: "Open viewer" }));
  expect(screen.getByText("Mounted viewer")).toBeTruthy();
  await user.click(screen.getByRole("button", { name: "Return control" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1].headers.get("If-Match")).toBe('"browser_1:4"');
  expect(screen.queryByText("Mounted viewer")).toBeNull();
  expect(screen.queryByRole("button", { name: "Delete browser" })).toBeNull();
});
it("offers start but not takeover for a retained stopped Browser", async () => {
  browser = { ...browser, status: "stopped" };
  start();
  await userEvent.click(
    await screen.findByRole("button", { name: "Start browser" }),
  );
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].url).toContain("/prepare");
  expect(screen.queryByRole("button", { name: "Take control" })).toBeNull();
});
it("shows the legacy terminal idle timeout in Browser details", async () => {
  browser = {
    ...browser,
    idle_seconds: 1800,
    stop_after_seconds: null,
    delete_after_seconds: null,
  };
  options = [browser];
  render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <BrowsersPage />
      </MemoryRouter>
    </QueryClientProvider>,
  );
  await userEvent.click(await screen.findByText("Retained browser"));
  await screen.findByText("Delete after idle");
  expect(screen.getByText("1800 seconds")).toBeTruthy();
  expect(screen.getAllByText("Never")).toHaveLength(1);
});
it("stops the observed generation and keeps deletion on the resource surface", async () => {
  start(true);
  await userEvent.click(screen.getByRole("button", { name: "Stop browser" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].url).toContain("/stop");
  expect(requests[0].headers.get("If-Match")).toBe('"browser_1:3"');
  expect(screen.getByRole("button", { name: "Delete browser" })).toBeTruthy();
});
