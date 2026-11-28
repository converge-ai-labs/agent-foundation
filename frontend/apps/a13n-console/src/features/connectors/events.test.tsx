import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { render, screen, waitFor } from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { MemoryRouter } from "react-router";
import { afterEach, expect, it, vi } from "vitest";
import type { Schema } from "../../shared/api";
import { ConnectorEvents } from "./events";

const http = vi.hoisted(() => ({
  GET: vi.fn(),
  PATCH: vi.fn(),
  POST: vi.fn(),
}));
const permissions = vi.hoisted(() => ({ admin: true, write: true }));
vi.mock("../../auth/context", () => ({
  useClient: () => ({ workspace: () => http }),
}));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    workspace: { id: "ws_fixture" },
    basePath: "/workspace/ws_fixture",
    can: (verb: string) =>
      verb === "admin"
        ? permissions.admin
        : verb === "write"
          ? permissions.write
          : true,
  }),
}));
vi.mock("react-i18next", () => ({
  useTranslation: () => ({
    t: (key: string) => key,
    i18n: { resolvedLanguage: "en" },
  }),
}));
const connection = {
  id: "conn_fixture",
  connector_provider_id: "cprov_fixture",
  config: { app: "github" },
  status: "ready",
} as Schema["Connection"];
const trigger = {
  id: "ctrig_fixture",
  name: "Issue triage",
  event_type: "GITHUB_ISSUE",
  state: "enabled",
  version: 3,
  connection_id: connection.id,
  target: { mode: "store_only" },
  definition: { app: "github" },
  operation_kind: null,
};
const cache = new QueryClient({
  defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
});
function mount(items: unknown[] = []) {
  http.GET.mockImplementation(async (path: string) => ({
    data: path.endsWith("event-webhook")
      ? {
          provider_id: "cprov_fixture",
          configured: true,
          url: "https://events.example/callback",
          version: 2,
        }
      : {
          items: path === "/api/v1/connector-triggers" ? items : [],
          next_cursor: null,
        },
    response: new Response(),
  }));
  return render(
    <QueryClientProvider client={cache}>
      <MemoryRouter>
        <ConnectorEvents connection={connection} />
      </MemoryRouter>
    </QueryClientProvider>,
  );
}
afterEach(() => {
  cache.clear();
  vi.resetAllMocks();
  permissions.admin = true;
  permissions.write = true;
});

it("pauses with the observed version and explains accepted-event behavior", async () => {
  http.POST.mockResolvedValue({
    data: { ...trigger, state: "paused" },
    response: new Response(),
  });
  mount([trigger]);
  await userEvent
    .setup()
    .click(await screen.findByRole("button", { name: "Pause" }));
  await waitFor(() =>
    expect(http.POST).toHaveBeenCalledWith(
      "/api/v1/connector-triggers/{trigger_id}/{command}",
      {
        params: {
          path: { trigger_id: "ctrig_fixture", command: "pause" },
          header: { "If-Match": '"ctrig_fixture:3"' },
        },
      },
    ),
  );
  expect(
    screen.getByText(
      "Pausing or retiring a subscription affects new events. Events already accepted continue to run.",
    ),
  ).toBeTruthy();
});

it("keeps history visible without management controls for viewers", async () => {
  permissions.admin = false;
  permissions.write = false;
  mount([trigger]);
  await screen.findByText("Issue triage");
  expect(screen.queryByRole("button", { name: "Pause" })).toBeNull();
  expect(
    screen.queryByRole("button", { name: "Rotate signing key" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Add subscription" })).toBeNull();
  await userEvent
    .setup()
    .click(screen.getByRole("button", { name: /Issue triage/ }));
  expect(await screen.findByText("No events received yet.")).toBeTruthy();
});

it("uses each event's saved retry eligibility even after the subscription target changed", async () => {
  mount([trigger]);
  const original = http.GET.getMockImplementation()!;
  http.GET.mockImplementation(async (path: string, options: unknown) =>
    path === "/api/v1/connector-events"
      ? {
          data: {
            items: [
              {
                id: "cevt_failed",
                state: "failed",
                occurred_at: "2026-11-27T00:00:00Z",
                payload: { number: 1 },
                retryable: true,
                version: 4,
              },
            ],
            next_cursor: null,
          },
          response: new Response(),
        }
      : original(path, options),
  );
  await userEvent
    .setup()
    .click(await screen.findByRole("button", { name: /Issue triage/ }));
  await userEvent.setup().click(await screen.findByText(/failed/));
  await userEvent
    .setup()
    .click(await screen.findByRole("button", { name: "Retry delivery" }));
  await waitFor(() =>
    expect(http.POST).toHaveBeenCalledWith(
      "/api/v1/connector-events/{event_id}/retry",
      {
        params: {
          path: { event_id: "cevt_failed" },
          header: { "If-Match": '"cevt_failed:4"' },
        },
      },
    ),
  );
});

it("loads the native Thread only when expanded and links to its Session", async () => {
  mount([trigger]);
  const original = http.GET.getMockImplementation()!;
  http.GET.mockImplementation(async (path: string, options: unknown) => {
    if (path === "/api/v1/connector-events")
      return {
        data: {
          items: [
            {
              id: "cevt_submitted",
              state: "submitted",
              occurred_at: "2026-11-27T00:00:00Z",
              payload: { number: 1 },
              thread_id: "thr_event",
            },
          ],
          next_cursor: null,
        },
        response: new Response(),
      };
    if (path === "/api/v1/threads/{thread_id}")
      return {
        data: { id: "thr_event", session_id: "sess_event" },
        response: new Response(),
      };
    return original(path, options);
  });
  const user = userEvent.setup();
  await user.click(await screen.findByRole("button", { name: /Issue triage/ }));
  const summary = await screen.findByText(/submitted/);
  expect(
    http.GET.mock.calls.some(
      ([path]) => path === "/api/v1/threads/{thread_id}",
    ),
  ).toBe(false);
  await user.click(summary);
  expect(
    (
      await screen.findByRole("link", { name: "Open conversation" })
    ).getAttribute("href"),
  ).toBe("/workspace/ws_fixture/sessions/sess_event/threads/thr_event");
});

it("offers subscription recovery from server eligibility even without a failure message", async () => {
  mount([
    { ...trigger, operation_kind: "paused", failure: null, retryable: true },
  ]);
  await userEvent
    .setup()
    .click(
      await screen.findByRole("button", { name: "Retry subscription setup" }),
    );
  await waitFor(() =>
    expect(http.POST).toHaveBeenCalledWith(
      "/api/v1/connector-triggers/{trigger_id}/{command}",
      {
        params: {
          path: { trigger_id: "ctrig_fixture", command: "reconcile" },
          header: { "If-Match": '\"ctrig_fixture:3\"' },
        },
      },
    ),
  );
});

it("does not offer recovery for an unowned creation or label its failure as updating", async () => {
  mount([
    {
      ...trigger,
      operation_kind: "create_unconfirmed",
      failure: "subscription_creation_unconfirmed",
      retryable: false,
    },
  ]);
  await screen.findByRole("alert");
  expect(
    screen.queryByRole("button", { name: "Retry subscription setup" }),
  ).toBeNull();
  expect(screen.queryByText("Updating subscription…")).toBeNull();
});
