// @vitest-environment jsdom
import type { ReactNode } from "react";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, renderHook } from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import {
  createClient,
  type Client,
  type Notification,
} from "../../service-client";
import { conversationKeys, invalidateConversation } from "./api";
import { useConversationNotifications } from "./notifications";

let client: Client;
let cache: QueryClient;
let allowed = true;
let attachments: {
  options: Parameters<Client["notifications"]>[0];
  close: ReturnType<typeof vi.fn>;
}[];
vi.mock("../../auth/context", () => ({ useClient: () => client }));
vi.mock("../../layout/workspace", () => ({
  useWorkspace: () => ({
    basePath: "/workspace/design",
    workspace: { id: "workspace" },
    can: () => allowed,
  }),
}));
function wrapper({ children }: { children: ReactNode }) {
  return <QueryClientProvider client={cache}>{children}</QueryClientProvider>;
}
function notification(): Notification {
  return {
    type: "notification",
    schema_version: "1",
    notification_id: "notification_one",
    subscription_id: "console-conversation",
    workspace_id: "workspace",
    topic: "run.updated",
    resource_type: "run",
    resource_id: "run_one",
    resource_version: 2,
    session_id: "session_one",
    thread_id: "thread_one",
    run_id: "run_one",
    occurred_at: "2026-09-09T00:00:00Z",
  };
}
const keys = conversationKeys("workspace");
const own = [
  keys.sessions(),
  keys.threads("session_one"),
  keys.thread("thread_one"),
  keys.runs("thread_one"),
  keys.queue("thread_one"),
  keys.run("run_one"),
  keys.pending("run_one"),
  keys.items("run_one"),
];
const other = [
  keys.threads("session_other"),
  keys.thread("thread_other"),
  keys.run("run_other"),
  keys.pending("run_other"),
];
const outside = [
  conversationKeys("another_workspace").run("run_one"),
  ["models", "workspace"],
];
const invalid = (key: readonly unknown[]) =>
  cache.getQueryState(key)?.isInvalidated;
beforeEach(() => {
  allowed = true;
  attachments = [];
  cache = new QueryClient({
    defaultOptions: { queries: { retry: false, staleTime: Infinity } },
  });
  for (const key of [...own, ...other, ...outside]) cache.setQueryData(key, {});
  client = createClient({
    baseUrl: "https://service.example",
    auth: { type: "session" },
  });
  client.notifications = vi.fn((options) => {
    const close = vi.fn();
    attachments.push({ options, close });
    return { close };
  });
});
afterEach(() => {
  cleanup();
  cache.clear();
  client.close();
});

it("refreshes only a notification's Session, Thread and Run, preserving other scope caches", async () => {
  renderHook(() => useConversationNotifications(), { wrapper });
  await act(async () => {
    attachments[0]!.options.onNotification(notification());
  });
  expect(own.every(invalid)).toBe(true);
  expect([...other, ...outside].some(invalid)).toBe(false);
});

it("refreshes source and destination Runs together after a command accepts a successor", async () => {
  await invalidateConversation(
    cache,
    "workspace",
    { sessionId: "session_one", threadId: "thread_one", runId: "run_one" },
    {
      sessionId: "session_other",
      threadId: "thread_other",
      runId: "run_other",
    },
  );
  expect([...own, ...other].every(invalid)).toBe(true);
  expect(outside.some(invalid)).toBe(false);
});

it("widens reconciliation only for an observation gap and retains Workspace isolation", async () => {
  renderHook(() => useConversationNotifications(), { wrapper });
  await act(async () => {
    attachments[0]!.options.onState("gap");
  });
  expect([...own, ...other].every(invalid)).toBe(true);
  expect(outside.some(invalid)).toBe(false);
});

it("reconciles manual reconnection and ignores callbacks from a detached attachment", async () => {
  const hook = renderHook(() => useConversationNotifications(), { wrapper });
  await act(async () => {
    hook.result.current.reconnect();
  });
  expect(attachments).toHaveLength(2);
  expect(attachments[0]!.close).toHaveBeenCalledOnce();
  expect([...own, ...other].every(invalid)).toBe(true);
  for (const key of [...own, ...other]) cache.setQueryData(key, {});
  await act(async () => {
    attachments[0]!.options.onState("gap");
    attachments[0]!.options.onNotification(notification());
    attachments[1]!.options.onNotification({
      ...notification(),
      workspace_id: "another_workspace",
    });
  });
  expect([...own, ...other, ...outside].some(invalid)).toBe(false);
});

it("does not attach notifications without subscription permission", () => {
  allowed = false;
  renderHook(() => useConversationNotifications(), { wrapper });
  expect(attachments).toHaveLength(0);
});
