// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { MemoryRouter } from "react-router";
import { ApiError, NetworkError, type Transport } from "../transport/client";
import { TransportContext } from "../transport/context";
import { Conversation } from "./conversation";
import { ComposerDrafts } from "./composer";
import { ThreadDraft } from "./draft";
import { previewInput } from "./local-input";

vi.mock("./live-threads", async () => {
  const { FocusDisplay } = await import("./stream");
  const snapshot = {
    display: new FocusDisplay(),
    connection: "Loading current output",
    reconnections: 0,
    revision: 0,
  };
  return { useLiveThread: () => snapshot };
});

it("retains an accepted input when the following detail read fails, without resending it", async () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  const GET = vi.fn().mockRejectedValue(new NetworkError(null));
  const POST = vi.fn();
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
  draft.localInputs = [
    {
      id: "accepted",
      action: "send",
      state: "accepted",
      parts: previewInput("accepted", ["Your first message"]),
    },
  ];
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { GET, POST } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["created", draft]])}>
          <MemoryRouter>
            <Conversation
              threadId="created"
              profile={{ display_name: "Tester", color: "#666666" }}
              unauthorized={() => {}}
            />
          </MemoryRouter>
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  try {
    await screen.findByText(/Unable to reach the server/);
    expect(
      screen.getAllByRole("alert").map((node) => node.textContent),
    ).toEqual([expect.stringContaining("Unable to reach the server")]);
    expect(screen.getByText("Your first message")).toBeTruthy();
    expect(
      screen.getByRole("region", { name: "Message composer" }),
    ).toBeTruthy();
    expect(
      screen.queryByRole("region", { name: "Chat unavailable" }),
    ).toBeNull();
    expect(draft.localInputs[0].state).toBe("accepted");
    expect(POST).not.toHaveBeenCalled();
  } finally {
    view.unmount();
    query.clear();
    draft.doc.destroy();
  }
});

beforeEach(() => {
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => new DOMRect(),
  });
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});

it("owns a missing chat once despite dependent failures, keeps unsent input, and retries only the read", async () => {
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  const error = new ApiError("Thread does not exist.", 400, "thread_missing");
  const GET = vi.fn().mockRejectedValue(error);
  const POST = vi.fn();
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
  draft.doc.getText("text").insert(0, "Keep this unsent input");
  draft.error = error.message;
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext
        value={{ client: { GET, POST } } as unknown as Transport}
      >
        <ComposerDrafts value={new Map([["missing", draft]])}>
          <MemoryRouter>
            <Conversation
              threadId="missing"
              profile={{ display_name: "Tester", color: "#666666" }}
              unauthorized={() => {}}
            />
          </MemoryRouter>
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
  try {
    await screen.findByRole("heading", { name: "Chat not found" });
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(screen.queryByText("Thread does not exist.")).toBeNull();
    expect(
      screen.queryByText(
        /Loading current output|Loading saved history|Refresh agent/,
      ),
    ).toBeNull();
    expect(
      screen.queryByRole("region", { name: "Message composer" }),
    ).toBeNull();
    expect(
      screen.getByText("Your unsent input is still in this tab."),
    ).toBeTruthy();
    const reads = () =>
      GET.mock.calls.filter(([path]) => path === "/api/threads/{thread_id}")
        .length;
    expect(reads()).toBe(1);
    fireEvent.click(screen.getByRole("button", { name: "Retry" }));
    await waitFor(() => expect(reads()).toBe(2));
    expect(POST).not.toHaveBeenCalled();
    expect(draft.doc.getText("text").toString()).toBe("Keep this unsent input");
    expect(screen.getAllByRole("alert")).toHaveLength(1);
    expect(
      screen.getByRole("link", { name: "New chat" }).getAttribute("href"),
    ).toBe("/");
  } finally {
    view.unmount();
    query.clear();
    draft.doc.destroy();
  }
});

it("never blocks a prompt-permitted guest on an ACP thread", async () => {
  const { GuestAccessContext } = await import("../transport/context");
  const { encode } = await import("./draft");
  const Y = await import("yjs");
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  const GET = vi.fn(async (path: string) => {
    if (path === "/api/threads/{thread_id}")
      return {
        data: {
          thread: {
            thread_id: "guest-acp",
            title: "Shared ACP",
            role: "ordinary",
            archived: false,
            metadata_version: 1,
            root_activity: { state: "inactive" },
            configuration: {
              version: 1,
              agent_source: { kind: "acp_agent", id: "acp-agy" },
              local_roots: [],
            },
          },
          continuation_id: "c1",
          available_actions: ["run"],
        },
      };
    if (path === "/api/threads/{thread_id}/transcript")
      return { data: { entries: [], turns: [], next_cursor: null } };
    if (path === "/api/threads/{thread_id}/work")
      return {
        data: { tasks: { completed: 0, total: 0 }, notes: { total: 0 } },
      };
    if (path === "/api/threads/{thread_id}/acp-options")
      throw new Error("Guest must not fetch ACP options");
    return { data: {} };
  });
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false } },
  });
  const draft = new ThreadDraft();
  vi.spyOn(draft, "connect").mockReturnValue({ presence() {}, close() {} });
  draft.doc.getText("text").insert(0, "Guest prompt");
  draft.receive({
    draft_id: "draft-guest",
    participant_id: "guest",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
  });
  const view = render(
    <QueryClientProvider client={query}>
      <TransportContext
        value={
          {
            shareId: "share-1",
            client: { GET, POST: vi.fn() },
          } as unknown as Transport
        }
      >
        <GuestAccessContext
          value={
            {
              grant: {
                share_id: "share-1",
                thread_id: "guest-acp",
                permission: "prompt",
              },
            } as never
          }
        >
          <ComposerDrafts value={new Map([["guest-acp", draft]])}>
            <MemoryRouter>
              <Conversation
                threadId="guest-acp"
                profile={{ display_name: "Guest", color: "#666666" }}
                unauthorized={() => {}}
              />
            </MemoryRouter>
          </ComposerDrafts>
        </GuestAccessContext>
      </TransportContext>
    </QueryClientProvider>,
  );
  try {
    const send = (await screen.findByRole("button", {
      name: "Send",
    })) as HTMLButtonElement;
    await waitFor(() => expect(send.disabled).toBe(false));
    expect(
      GET.mock.calls.some(
        ([path]) => path === "/api/threads/{thread_id}/acp-options",
      ),
    ).toBe(false);
  } finally {
    view.unmount();
    query.clear();
    draft.doc.destroy();
  }
});

it("classifies ACP loading, refreshing, missing-agent, auth, and generic errors cleanly", async () => {
  const { acpRunBlock } = await import("./acp-options");
  const { i18n } = await import("../i18n");
  await i18n.changeLanguage("en");
  const base = {
    agentName: "Antigravity",
    hasData: false,
    isFetching: false,
    isError: false,
    error: null as unknown,
    onRetryLoad: vi.fn(),
    updating: false,
    updateError: null as unknown,
    onRetryUpdate: vi.fn(),
    onDismissUpdateError: vi.fn(),
    t: i18n.t.bind(i18n),
  };

  expect(acpRunBlock({ ...base, hasData: true, isFetching: true })).toEqual({
    kind: "loading",
    message: "Refreshing Antigravity options…",
  });
  expect(acpRunBlock({ ...base, hasData: true, updating: true })).toEqual({
    kind: "loading",
    message: "Updating Antigravity options…",
  });

  // TanStack Query v5 retry: isFetching takes precedence over retained isError.
  expect(
    acpRunBlock({
      ...base,
      isFetching: true,
      isError: true,
      error: new ApiError("Timed out", 504, "acp_session_unavailable"),
    }),
  ).toEqual({
    kind: "loading",
    message: "Loading Antigravity options…",
  });

  const missing = acpRunBlock({
    ...base,
    isError: true,
    error: new ApiError("Not found", 404, "acp_agent_missing"),
  });
  expect(missing?.kind).toBe("error");
  expect(missing?.coveredByFeedback).toBeUndefined();
  expect(missing?.message).toBe(
    "The selected ACP agent is no longer available. Choose another agent or check Settings.",
  );
  expect(missing?.action?.label).toBe("Retry");

  const generic = acpRunBlock({
    ...base,
    isError: true,
    error: new ApiError("ACP daemon closed", 503, "acp_session_unavailable"),
  });
  expect(generic?.kind).toBe("error");
  expect(generic?.coveredByFeedback).toBeUndefined();
  expect(generic?.message).toBe("Could not load Antigravity options.");
  expect(generic?.detail).toBe("ACP daemon closed");
  expect(generic?.action?.label).toBe("Retry");

  const authWithoutPath = acpRunBlock({
    ...base,
    isError: true,
    error: new ApiError("Auth needed", 401, "acp_auth_required"),
  });
  expect(authWithoutPath?.kind).toBe("action_required");
  expect(authWithoutPath?.coveredByFeedback).toBeUndefined();
  expect(authWithoutPath?.action?.label).toBe("Retry");
  expect(authWithoutPath?.secondaryAction).toBeUndefined();

  expect(acpRunBlock({ ...base, hasData: true })).toBeNull();
});
