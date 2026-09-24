// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  within,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Y from "yjs";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import { Realtime } from "../transport/realtime";
import { ComposerDrafts } from "./composer";
import { previewInput } from "./local-input";
import { ThreadDraft, encode, values } from "./draft";
import {
  NewConversationDrafts,
  NewConversationPage,
  newConversationPath,
} from "./new-conversation";
import { NewDraftStore } from "./new-draft";
import { ConversationPage } from "./conversation";
import { LiveThreadsProvider } from "./live-threads";
import type { Schema } from "../transport/client";

const id = `thread_${"a".repeat(32)}`;
const path = newConversationPath("project-one");
const ownerId = `thread_${"c".repeat(32)}`;
let ownerArchived = false;
let createdOwner: string | undefined;
let writes: Request[];
let reads: string[];
let failure: "create" | "submit" | "reject" | "submit-reject" | null;
let paused: Promise<void> | undefined;
let readPaused: Promise<void> | undefined;
let readFailure = false;
let historyPaused: Promise<void> | undefined;
let focusPaused: Promise<void> | undefined;
let focusUnavailable = false;
let focused: Schema<"ThreadFocusSnapshot"> | undefined;
const threadDetail: Schema<"ThreadDetail"> = {
  thread: {
    thread_id: id,
    created_at: "2026-09-16",
    updated_at: "2026-09-16",
    metadata_version: 1,
    archived: false,
    continuation_state: "initial",
    root_activity: { state: "inactive" },
    configuration: {
      version: 1,
      project_id: "project-one",
      agent_source: { kind: "agent", id: "agent-one" },
      environment_profile_id: "environment-native",
    } as Schema<"ThreadConfigurationView">,
  },
  continuation_id: null,
  available_actions: ["run"],
};
let drafts: Map<string, ThreadDraft>;
let creations: NewDraftStore;
let queries: QueryClient;
function json(data: unknown, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { "Content-Type": "application/json" },
  });
}
beforeEach(() => {
  writes = [];
  reads = [];
  ownerArchived = false;
  createdOwner = undefined;
  failure = null;
  paused = undefined;
  readPaused = undefined;
  readFailure = false;
  historyPaused = undefined;
  focusPaused = undefined;
  focused = undefined;
  focusUnavailable = false;
  vi.spyOn(Realtime.prototype, "subscribe").mockImplementation(
    (subscription) => {
      let active = true;
      subscription.state("Connecting");
      const emit = () =>
        void Promise.resolve(focusPaused).then(() => {
          if (!active) return;
          if (focusUnavailable) {
            subscription.state("Reconnecting");
            return;
          }
          subscription.receive({
            kind: "snapshot",
            resume_cursor: "cursor-one",
            snapshot: focused ?? {
              epoch: "epoch-one",
              cutover_sequence: 0,
              thread: createdOwner
                ? {
                    ...threadDetail,
                    thread: {
                      ...threadDetail.thread,
                      role: "worker",
                      coordinator_thread_id: createdOwner,
                    },
                  }
                : threadDetail,
            },
          });
        });
      emit();
      const close = () => {
        active = false;
      };
      close.restart = emit;
      close.retry = emit;
      return close;
    },
  );
  drafts = new Map();
  localStorage.clear();
  creations = new NewDraftStore();
  creations.get(drafts, id);
  queries = new QueryClient({
    defaultOptions: {
      queries: { retry: false, staleTime: Infinity },
      mutations: { retry: false },
    },
  });
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe() {}
      unobserve() {}
      disconnect() {}
    },
  );
  vi.stubGlobal("matchMedia", () => ({
    matches: false,
    addEventListener() {},
    removeEventListener() {},
  }));
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  Object.defineProperty(Range.prototype, "getBoundingClientRect", {
    configurable: true,
    value: () => ({
      left: 0,
      top: 0,
      right: 0,
      bottom: 0,
      width: 0,
      height: 0,
    }),
  });
  vi.spyOn(ThreadDraft.prototype, "connect").mockImplementation(function (
    this: ThreadDraft,
  ) {
    let active = true;
    const acknowledge = () =>
      queueMicrotask(() => {
        if (active && !this.synchronized)
          this.receive({
            draft_id: "draft-one",
            participant_id: "participant-one",
            participants: {},
            update_base64: encode(Y.encodeStateAsUpdate(this.doc)),
          });
      });
    this.doc.on("update", acknowledge);
    acknowledge();
    return {
      presence() {},
      close: () => {
        active = false;
        this.doc.off("update", acknowledge);
      },
    };
  });
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const url = new URL(request.url);
      const pathname = url.pathname;
      if (request.method === "GET") {
        reads.push(pathname);
        if (pathname === "/api/projects")
          return json([
            { project_id: "project-one", name: "Example project", roots: [] },
            { project_id: "project-two", name: "Second project", roots: [] },
          ]);
        if (pathname === "/api/setup") return json({ needed: false });
        if (pathname === "/api/selectors")
          return json({
            agents: [
              { agent_id: "agent-one", name: "Writer", model_id: "model-one" },
              {
                agent_id: "agent-two",
                name: "Reviewer",
                model_id: "model-two",
              },
            ],
            models: [
              {
                model_id: "model-one",
                name: "Primary model",
                route: "custom:primary",
                thinking: {
                  status: "supported",
                  default_summary: "High",
                  options: [
                    {
                      value: null,
                      label: "Model default",
                      description: "High",
                    },
                    {
                      value: "low",
                      label: "Quick thinking",
                      description: "Published by the backend",
                    },
                  ],
                },
              },
              {
                model_id: "model-two",
                name: "Other model",
                route: "openai:other",
              },
            ],
            environments: [
              {
                profile_id: "environment-native",
                name: "Full Control",
                mode: "full-control",
              },
              {
                profile_id: "environment-sandbox",
                name: "Sandbox",
                mode: "sandbox",
                description: "Isolated local execution without networking.",
              },
            ],
          });
        if (pathname === `/api/threads/${ownerId}`)
          return json({
            ...threadDetail,
            thread: {
              ...threadDetail.thread,
              thread_id: ownerId,
              title: "Project manager",
              role: "coordinator",
              archived: ownerArchived,
            },
          });
        if (pathname === `/api/threads/${id}`) {
          const detail =
            reads.filter((path) => path === pathname).length === 1
              ? threadDetail
              : (focused?.thread ?? threadDetail);
          await readPaused;
          if (readFailure)
            return json(
              { error: { message: "Thread detail unavailable" } },
              500,
            );
          return json(
            createdOwner
              ? {
                  ...detail,
                  thread: {
                    ...detail.thread,
                    role: "worker",
                    coordinator_thread_id: createdOwner,
                  },
                }
              : detail,
          );
        }
        if (pathname.endsWith("/tasks")) return json({ tasks: [] });
        if (pathname.endsWith("/children"))
          return json({ executions: [], total: 0 });
        if (pathname.endsWith("/usage"))
          return json({
            root: { tokens: [], known_cost: "0", unknown_model_costs: 0 },
          });
        if (pathname.endsWith("/context-usage") || pathname.endsWith("/skills"))
          return json({});
        if (pathname === "/api/threads/activity")
          return json({ rows: [], active_rows: [], total: 0 });
        if (pathname.startsWith("/api/operations/"))
          return json({
            receipt: { receipt_id: "receipt-one", thread_id: id },
            status: "completed",
          });
        if (pathname === `/api/threads/${id}/transcript`) {
          await historyPaused;
          return json({
            continuation_id: "initial:one",
            entries: [],
            next_cursor: null,
          });
        }
        throw new Error(`Unexpected read ${url}`);
      }
      if (pathname.endsWith("configuration-preview"))
        return json({
          configuration: {
            project_id: (await request.clone().json()).project_id ?? null,
            agent_source: {
              id: (await request.clone().json()).agent_id || "agent-one",
            },
            environment_profile_id: "environment-native",
          },
        });
      writes.push(request.clone());
      if (pathname === "/api/threads") {
        createdOwner = (await request.clone().json()).coordinator_thread_id;
        await paused;
        if (failure === "create")
          throw new TypeError("Creation acknowledgement lost");
        if (failure === "reject")
          return json({ error: { message: "Configuration not ready" } }, 400);
        return json({
          thread_id: id,
          ...(createdOwner
            ? { role: "worker", coordinator_thread_id: createdOwner }
            : {}),
        });
      }
      if (pathname.endsWith("/attachments"))
        return json({
          attachment_id: "attachment-one",
          name: "notes.txt",
          size: 5,
          media_type: "text/plain",
        });
      if (pathname.endsWith("/submit")) {
        if (failure === "submit-reject")
          return json({ error: { message: "Conversation busy" } }, 409);
        if (failure === "submit")
          throw new TypeError("Submission acknowledgement lost");
        return json({ thread_id: id, receipt_id: "receipt-one" });
      }
      throw new Error(`Unexpected request ${request.method} ${url}`);
    }),
  );
});
afterEach(() => {
  cleanup();
  creations.dispose();
  queries.clear();
  for (const draft of drafts.values()) {
    draft.undo.destroy();
    draft.doc.destroy();
  }
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function Location() {
  const location = useLocation();
  return (
    <>
      <output aria-label="Location">{location.pathname}</output>
      <output aria-label="Search">{location.search}</output>
    </>
  );
}
function mount(initial = path) {
  return render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("test", () => {})}>
        <ComposerDrafts value={drafts}>
          <NewConversationDrafts value={creations}>
            <MemoryRouter initialEntries={[initial]}>
              <LiveThreadsProvider>
                <Link to="/settings">Settings</Link>
                <Link to={initial}>Return to draft</Link>
                <Link to="/">Home</Link>
                <Link to={newConversationPath("project-two")}>
                  New in second project
                </Link>
                <Link to={newConversationPath()}>New without project</Link>
                <Location />
                <Routes>
                  <Route
                    path="/new/:draftId?"
                    element={
                      <NewConversationPage
                        profile={{ display_name: "Test", color: "#000000" }}
                        unauthorized={() => {}}
                      />
                    }
                  />
                  <Route
                    path="/"
                    element={
                      <NewConversationPage
                        profile={{ display_name: "Test", color: "#000000" }}
                        unauthorized={() => {}}
                      />
                    }
                  />
                  <Route
                    path="/threads/:threadId"
                    element={
                      <ConversationPage
                        profile={{ display_name: "Test", color: "#000000" }}
                        unauthorized={() => {}}
                      />
                    }
                  />
                  <Route path="*" element={<p>Other page</p>} />
                </Routes>
              </LiveThreadsProvider>
            </MemoryRouter>
          </NewConversationDrafts>
        </ComposerDrafts>
      </TransportContext>
    </QueryClientProvider>,
  );
}
async function fill() {
  await screen.findByRole("heading", {
    name: "What would you like to build in Example project?",
  });
  await waitFor(() =>
    expect(
      screen.getByRole("combobox", { name: "Agent" }).textContent,
    ).toContain("Writer"),
  );
  act(() => drafts.get(id)!.doc.getText("text").insert(0, "Build this"));
}

it("keeps the blank composer and files local, then creates, uploads, synchronizes and sends exactly once", async () => {
  let resume!: () => void;
  paused = new Promise<void>((resolve) => {
    resume = resolve;
  });
  const view = mount();
  await fill();
  fireEvent.change(view.container.querySelector('input[type="file"]')!, {
    target: {
      files: [new File(["notes"], "notes.txt", { type: "text/plain" })],
    },
  });
  await screen.findByRole("button", { name: "notes.txt · ready to upload" });
  expect(writes).toHaveLength(0);
  // Workbench discovery may read activity, but blank input has no Thread yet.
  expect(
    reads.some(
      (item) =>
        item.startsWith("/api/threads/") && item !== "/api/threads/activity",
    ),
  ).toBe(false);
  expect(ThreadDraft.prototype.connect).not.toHaveBeenCalled();
  const send = screen.getByRole("button", { name: "Send" });
  fireEvent.click(send);
  fireEvent.click(send);
  await waitFor(() => expect(writes).toHaveLength(1));
  expect(screen.getByLabelText("Location").textContent).toBe("/new");
  expect(view.container.querySelector("[data-message-id]")).toBeNull();
  expect(values(drafts.get(id)!.doc).prompt).toContain("Build this");
  expect(screen.getByRole("button", { name: "Submitting" })).toBeTruthy();
  await act(async () => resume());
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
  expect(writes.map((request) => new URL(request.url).pathname)).toEqual([
    "/api/threads",
    `/api/threads/${id}/attachments`,
    `/api/threads/${id}/submit`,
  ]);
  expect(await writes[0].json()).toEqual({
    thread_id: id,
    defaults: { project_id: "project-one" },
  });
  expect(await writes[2].json()).toEqual({
    mode: "normal",
    parts: [{ attachment_id: "attachment-one" }, "Build this"],
    source_id: expect.stringMatching(/^input_[0-9a-f]{32}$/),
  });
  expect(values(drafts.get(id)!.doc).prompt).toBe("");
});

it("retains rejected creation input and permits an explicit retry with the same identity", async () => {
  failure = "reject";
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Configuration not ready");
  expect(values(drafts.get(id)!.doc).prompt).toBe("Build this");
  expect(creations.current!.attempted).toBe(false);
  failure = null;
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
  expect((await writes[0].json()).thread_id).toBe(
    (await writes[1].json()).thread_id,
  );
});

it("reconciles uncertain creation by exact read and opens the existing thread without submitting", async () => {
  failure = "create";
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText(
    "Unable to reach the server. Check your connection and try again.",
  );
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
  expect(reads).toContain(`/api/threads/${id}`);
  expect(writes).toHaveLength(1);
  expect(values(drafts.get(id)!.doc).prompt).toBe("Build this");
});

it("retains an uncertain submission on the New page without a speculative bubble or replay", async () => {
  failure = "submit";
  const view = mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(drafts.get(id)!.submission.kind).toBe("unknown"));
  expect(screen.getByLabelText("Location").textContent).toBe("/new");
  expect(view.container.querySelector("[data-message-id]")).toBeNull();
  expect(
    screen.queryByRole("button", { name: "I reviewed the outcome" }),
  ).toBeNull();
  expect(values(drafts.get(id)!.doc).prompt).toBe("Build this");
  expect(writes).toHaveLength(2);
  fireEvent.click(
    screen.getByRole("button", { name: "Review submission in conversation" }),
  );
  await screen.findByRole("button", { name: "Refresh operation and history" });
  expect(screen.getByLabelText("Location").textContent).toBe(`/threads/${id}`);
  expect(reads).toContain(`/api/threads/${id}/transcript`);
  expect(drafts.get(id)!.submission.kind).toBe("unknown");
  expect(writes).toHaveLength(2);
});

it("keeps rejected first input on the New page and retries without recreating the Thread", async () => {
  failure = "submit-reject";
  const view = mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Conversation busy");
  expect(screen.getByLabelText("Location").textContent).toBe("/new");
  expect(view.container.querySelector("[data-message-id]")).toBeNull();
  expect(values(drafts.get(id)!.doc).prompt).toBe("Build this");
  expect(writes).toHaveLength(2);
  failure = null;
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
  expect(writes.map((request) => new URL(request.url).pathname)).toEqual([
    "/api/threads",
    `/api/threads/${id}/submit`,
    `/api/threads/${id}/submit`,
  ]);
  expect(values(drafts.get(id)!.doc).prompt).toBe("");
});

it("does not send after navigating away during creation and preserves the draft for return", async () => {
  let resume!: () => void;
  paused = new Promise<void>((resolve) => {
    resume = resolve;
  });
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(writes).toHaveLength(1));
  fireEvent.click(screen.getByRole("link", { name: "Settings" }));
  await act(async () => resume());
  expect(writes).toHaveLength(1);
  expect(screen.getByLabelText("Location").textContent).toBe("/settings");
  fireEvent.click(screen.getByRole("link", { name: "Return to draft" }));
  await screen.findByRole("textbox", { name: "Shared prompt" });
  expect(values(drafts.get(id)!.doc).prompt).toBe("Build this");
  expect(writes).toHaveLength(1);
});

it("does not steal navigation when an uncertain creation is reconciled after leaving the page", async () => {
  failure = "create";
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText(
    "Unable to reach the server. Check your connection and try again.",
  );
  let resume!: () => void;
  readPaused = new Promise<void>((resolve) => {
    resume = resolve;
  });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(reads).toContain(`/api/threads/${id}`));
  fireEvent.click(screen.getByRole("link", { name: "Settings" }));
  await act(async () => resume());
  expect(creations.current!.created).toBe(true);
  expect(screen.getByLabelText("Location").textContent).toBe("/settings");
  expect(writes).toHaveLength(1);
});

it("distinguishes inherited choices and sends an independent model without changing the agent binding", async () => {
  const user = (await import("@testing-library/user-event")).default.setup();
  mount();
  await fill();
  expect(screen.queryByRole("button", { name: "Composer help" })).toBeNull();
  await user.click(screen.getByRole("combobox", { name: "Agent" }));
  expect(
    await screen.findByRole("option", { name: /Writer.*Default/ }),
  ).toBeTruthy();
  expect(
    screen.getByRole("option", { name: /Writer.*agent-one/ }),
  ).toBeTruthy();
  await user.click(screen.getByRole("option", { name: /Reviewer.*agent-two/ }));
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
  await user.click(screen.getByRole("button", { name: "Model settings" }));
  await user.click(screen.getByRole("button", { name: "Change model" }));
  expect(
    (await screen.findByRole("button", { name: /Agent default/ })).textContent,
  ).toContain("Other model");
  await user.click(screen.getByRole("button", { name: "Primary model" }));
  await user.click(screen.getByRole("button", { name: "Quick thinking" }));
  await user.keyboard("[Escape]");
  await user.click(screen.getByRole("link", { name: "Settings" }));
  await user.click(screen.getByRole("link", { name: "Return to draft" }));
  expect(
    screen.getByRole("button", { name: "Model settings" }).textContent,
  ).toContain("Primary model");
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  await user.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(writes).toHaveLength(2));
  expect(await writes[0].json()).toEqual({
    thread_id: id,
    defaults: { project_id: "project-one", agent_id: "agent-two" },
  });
  expect(await writes[1].json()).toEqual({
    mode: "normal",
    parts: ["Build this"],
    source_id: expect.stringMatching(/^input_[0-9a-f]{32}$/),
    model_id: "model-one",
    thinking: "low",
  });
});

it("reveals the new conversation together after detail, history and editor initialize", async () => {
  let resume!: () => void;
  let resumeHistory!: () => void;
  readPaused = focusPaused = new Promise<void>((resolve) => {
    resume = resolve;
  });
  historyPaused = new Promise<void>((resolve) => {
    resumeHistory = resolve;
  });
  const view = mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(
      vi
        .mocked(Realtime.prototype.subscribe)
        .mock.calls.some(([channel]) => channel.root === id),
    ).toBe(true),
  );
  const editor = view.container.querySelector('[role="textbox"]')!;
  const input = screen.getByText("Build this");
  expect(screen.getByLabelText("Location").textContent).toBe(`/threads/${id}`);
  expect(screen.queryByRole("textbox", { name: "Shared prompt" })).toBeNull();
  expect(screen.getByText("Opening conversation…")).toBeTruthy();
  expect(writes).toHaveLength(2);
  await act(async () => resume());
  await waitFor(() => expect(reads).toContain(`/api/threads/${id}/transcript`));
  expect(drafts.get(id)!.status).toBe("Connected");
  expect(screen.queryByRole("textbox", { name: "Shared prompt" })).toBeNull();
  await act(async () => resumeHistory());
  expect(await screen.findByRole("textbox", { name: "Shared prompt" })).toBe(
    editor,
  );
  expect(screen.queryByText("Opening conversation…")).toBeNull();
  expect(document.activeElement).toBe(editor);
  expect(screen.getByText("Build this")).toBe(input);
  expect(screen.queryByText("Start something together.")).toBeNull();
  expect(writes).toHaveLength(2);
  // Later observations do not replace the composer or re-enter loading.
  act(() => drafts.get(id)!.doc.getText("text").insert(0, "Follow up"));
  await act(async () => {
    await queries.invalidateQueries({ queryKey: ["thread", id] });
  });
  expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBe(editor);
  expect(editor.textContent).toBe("Follow up");
  expect(document.activeElement).toBe(editor);
  expect(screen.queryByText("Opening conversation…")).toBeNull();
});

function reload(initial = path) {
  cleanup();
  creations.dispose();
  for (const draft of drafts.values()) {
    draft.undo.destroy();
    draft.doc.destroy();
  }
  drafts = new Map();
  creations = new NewDraftStore();
  return mount(initial);
}

it("uses one composer across project plus actions, Home and settings without creating Threads", async () => {
  mount();
  await fill();
  const editor = screen.getByRole("textbox", { name: "Message" });
  const original = creations.current!;
  act(() => {
    original.composer.modelId = "model-two";
    original.composer.notify();
  });
  fireEvent.click(screen.getByRole("link", { name: "New in second project" }));
  await screen.findByRole("heading", {
    name: "What would you like to build in Second project?",
  });
  expect(screen.getByRole("textbox", { name: "Message" })).toBe(editor);
  expect(editor.textContent).toBe("Build this");
  expect(creations.current).toBe(original);
  expect(original.defaults.project_id).toBe("project-two");
  expect(original.composer.modelId).toBe("model-two");
  fireEvent.click(screen.getByRole("link", { name: "Settings" }));
  fireEvent.click(screen.getByRole("link", { name: "Home" }));
  await screen.findByRole("heading", {
    name: "What would you like to build in Second project?",
  });
  expect(screen.getByRole("textbox", { name: "Message" }).textContent).toBe(
    "Build this",
  );
  fireEvent.click(screen.getByRole("link", { name: "New without project" }));
  await screen.findByRole("heading", { name: "What would you like to build?" });
  expect(creations.current!.defaults.project_id).toBeNull();
  expect(values(original.composer.doc).prompt).toBe("Build this");
  expect(writes).toHaveLength(0);
  expect(screen.getByLabelText("Location").textContent).toBe("/new");
});

it("restores text and choices after a full reload and persists deleting the input", async () => {
  mount();
  await fill();
  act(() => {
    creations.current!.composer.modelId = "model-two";
    creations.current!.composer.notify();
  });
  fireEvent.click(screen.getByRole("link", { name: "New in second project" }));
  await screen.findByRole("heading", {
    name: "What would you like to build in Second project?",
  });
  reload("/");
  await screen.findByRole("heading", {
    name: "What would you like to build in Second project?",
  });
  expect(screen.getByRole("textbox", { name: "Message" }).textContent).toBe(
    "Build this",
  );
  expect(creations.current!.threadId).toBe(id);
  expect(
    screen.getByRole("button", { name: "Model settings" }).textContent,
  ).toContain("Other model");
  act(() => {
    const text = creations.current!.composer.doc.getText("text");
    text.delete(0, text.length);
  });
  reload("/");
  await screen.findByRole("textbox", { name: "Message" });
  expect(values(creations.current!.composer.doc).prompt).toBe("");
  expect(writes).toHaveLength(0);
});

it("creates the selected project only on Send, then starts a fresh singleton after acceptance", async () => {
  mount();
  await fill();
  fireEvent.click(screen.getByRole("link", { name: "New in second project" }));
  await screen.findByRole("heading", {
    name: "What would you like to build in Second project?",
  });
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
  expect((await writes[0].json()).defaults.project_id).toBe("project-two");
  expect(localStorage.getItem("a13n-harness-ui.new-draft")).toBeNull();
  fireEvent.click(screen.getByRole("link", { name: "New in second project" }));
  await screen.findByRole("textbox", { name: "Message" });
  expect(values(creations.current!.composer.doc).prompt).toBe("");
  expect(creations.current!.threadId).not.toBe(id);
  expect(writes).toHaveLength(2);
});

it("retains the exact identity and frozen project when creation is uncertain across reload", async () => {
  failure = "create";
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText(
    "Unable to reach the server. Check your connection and try again.",
  );
  reload(newConversationPath("project-two"));
  await screen.findByRole("heading", {
    name: "What would you like to build in Example project?",
  });
  expect(creations.current!.attempted).toBe(true);
  expect(creations.current!.threadId).toBe(id);
  expect(screen.getByRole("textbox", { name: "Message" }).textContent).toBe(
    "Build this",
  );
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
  expect(reads).toContain(`/api/threads/${id}`);
  expect(writes).toHaveLength(1);
});

it("keeps an uncertain submission blocked after reload rather than replaying input", async () => {
  failure = "submit";
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(drafts.get(id)!.submission.kind).toBe("unknown"));
  expect(screen.getByLabelText("Location").textContent).toBe("/new");
  reload();
  await screen.findByText(
    /The previous input may already have been accepted\. Open the conversation/,
  );
  expect(creations.current!.composer.submission.kind).toBe("unknown");
  expect(
    (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(
    screen.getByRole("textbox", { name: "Shared prompt" }).textContent,
  ).toBe("Build this");
  expect(writes).toHaveLength(2);
});

it("retains staged files across project switches and explicitly requires reattachment after reload", async () => {
  const view = mount();
  await fill();
  fireEvent.change(view.container.querySelector('input[type="file"]')!, {
    target: {
      files: [new File(["notes"], "notes.txt", { type: "text/plain" })],
    },
  });
  fireEvent.click(screen.getByRole("link", { name: "New in second project" }));
  await screen.findByRole("heading", {
    name: "What would you like to build in Second project?",
  });
  expect(creations.current!.composer.uploads.size).toBe(1);
  reload("/");
  await screen.findByText(/Local files are not saved across reloads/);
  expect(values(creations.current!.composer.doc).prompt).toBe("Build this");
  expect(
    (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(writes).toHaveLength(0);
});

it("retains accepted input on a saved-page read failure and starts a fresh draft on plus", async () => {
  readFailure = true;
  focusUnavailable = true;
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Thread detail unavailable");
  expect(screen.getByLabelText("Location").textContent).toBe(`/threads/${id}`);
  expect(drafts.get(id)!.localInputs[0].state).toBe("accepted");
  expect(screen.queryByText("Not sent · input retained")).toBeNull();
  expect(creations.current).toBeUndefined();
  fireEvent.click(screen.getByRole("link", { name: "New in second project" }));
  await screen.findByRole("heading", {
    name: "What would you like to build in Second project?",
  });
  expect(creations.current!.threadId).not.toBe(id);
  expect(creations.current!.attempted).toBe(false);
  act(() =>
    creations.current!.composer.doc.getText("text").insert(0, "Next task"),
  );
  reload("/");
  expect(
    (await screen.findByRole("textbox", { name: "Message" })).textContent,
  ).toBe("Next task");
  expect(writes).toHaveLength(2);
});

it("preserves native deep-link parameters when restoring or changing Project", async () => {
  const user = (await import("@testing-library/user-event")).default.setup();
  mount("/?native=files&native_path=%2Ftmp%2Fnotes.txt&terminal=terminal-one");
  await screen.findByRole("textbox", { name: "Message" });
  await waitFor(() =>
    expect(screen.getByLabelText("Search").textContent).toContain("project="),
  );
  await user.click(screen.getByRole("combobox", { name: "Project" }));
  await user.click(
    await screen.findByRole("option", { name: "Second project" }),
  );
  await waitFor(() =>
    expect(creations.current!.defaults.project_id).toBe("project-two"),
  );
  const search = new URLSearchParams(
    screen.getByLabelText("Search").textContent!,
  );
  expect(search.get("native")).toBe("files");
  expect(search.get("native_path")).toBe("/tmp/notes.txt");
  expect(search.get("terminal")).toBe("terminal-one");
  expect(search.get("project")).toBe("project-two");
});

it("uses the focused first frame without waiting for a slow detail read or allowing it to roll status back", async () => {
  let release!: () => void;
  readPaused = new Promise<void>((resolve) => {
    release = resolve;
  });
  focused = {
    epoch: "epoch-one",
    cutover_sequence: 1,
    thread: {
      ...threadDetail,
      thread: {
        ...threadDetail.thread,
        root_activity: {
          state: "running",
          run_id: "run-one",
          receipt_id: "receipt-one",
          available_actions: ["cancel", "steer"],
        },
      },
      available_actions: ["cancel", "steer"],
    },
    root_operation: {
      receipt: { thread_id: id, receipt_id: "receipt-one" },
      status: "running",
    } as Schema<"RootOperationView">,
  };
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByRole("button", { name: "Stop" });
  const input = screen.getByText("Build this");
  expect(screen.getByLabelText("Location").textContent).toBe(`/threads/${id}`);
  expect(screen.queryByText("Loading saved history…")).toBeNull();
  expect(writes).toHaveLength(2);
  await act(async () => release());
  expect(screen.getByText("Build this")).toBe(input);
  expect(screen.getByRole("button", { name: "Stop" })).toBeTruthy();
  expect(drafts.get(id)!.localInputs[0].state).toBe("accepted");
});

it("keeps saved history at the real bottom after viewport resize without reclaiming a manual reading position", async () => {
  const targets = new Set<Element>();
  let resized!: () => void;
  vi.stubGlobal(
    "ResizeObserver",
    class {
      constructor(callback: () => void) {
        resized = callback;
      }
      observe(target: Element) {
        targets.add(target);
      }
      disconnect() {
        targets.clear();
      }
    },
  );
  const originalFetch = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (request) => {
    if (
      new URL(
        request instanceof Request ? request.url : String(request),
      ).pathname.endsWith("/transcript")
    )
      return json({
        continuation_id: "initial:one",
        entries: [
          {
            position: 0,
            message_kind: "response",
            parts: [{ kind: "assistant", text: "Saved response" }],
          },
        ],
        next_cursor: null,
      });
    return originalFetch(request);
  });
  const view = mount(`/threads/${id}`);
  await screen.findByText("Saved response");
  const reader = view.container.querySelector(
    '[class*="reading"]',
  )! as HTMLElement;
  let height = 600;
  let contentHeight = 2000;
  let top = 0;
  Object.defineProperties(reader, {
    scrollHeight: { get: () => contentHeight },
    clientHeight: { get: () => height },
    scrollTop: {
      get: () => top,
      set: (value: number) => {
        top = Math.max(0, Math.min(value, contentHeight - height));
      },
    },
  });
  expect(targets.has(reader)).toBe(true);
  act(() => resized());
  expect(top).toBe(1400);
  height = 560;
  act(() => resized());
  expect(top).toBe(1440);
  // Streaming commits must not start an animation for ResizeObserver to cancel.
  // Simulate a line arriving and then a Markdown reflow reducing its height.
  const frames = vi.spyOn(window, "requestAnimationFrame");
  for (const next of [2026, 2052, 2026]) {
    contentHeight = next;
    await act(async () => {
      queries.setQueriesData(
        { queryKey: ["thread", id, "history"] },
        {
          pages: [
            {
              continuation_id: "initial:one",
              entries: [
                {
                  position: 0,
                  message_kind: "response",
                  parts: [{ kind: "assistant", text: `Updated ${next}` }],
                },
              ],
              next_cursor: null,
            },
          ],
          pageParams: [undefined],
        },
      );
    });
    await screen.findByText(`Updated ${next}`);
    expect(top).toBe(contentHeight - height);
    act(() => resized());
    expect(top).toBe(contentHeight - height);
  }
  expect(frames).not.toHaveBeenCalled();
  frames.mockRestore();
  contentHeight = 2000;
  act(() => resized());
  fireEvent.wheel(reader, { deltaY: -30 });
  reader.scrollTop -= 30;
  fireEvent.scroll(reader);
  height = 540;
  act(() => resized());
  expect(top).toBe(1410);
  contentHeight += 100;
  act(() => resized());
  expect(top).toBe(1410);
});

it("waits for project, catalog and the selected defaults before exposing the new composer", async () => {
  const release = new Map<string, () => void>();
  const waits = new Map(
    [
      "/api/projects",
      "/api/selectors",
      "/api/threads/configuration-preview",
    ].map((path) => [
      path,
      new Promise<void>((resolve) => release.set(path, resolve)),
    ]),
  );
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (request) => {
    const path = new URL((request as Request).url).pathname;
    await waits.get(path);
    return original(request);
  });
  mount();
  expect(screen.getByText("Preparing your conversation…")).toBeTruthy();
  expect(screen.queryByRole("textbox")).toBeNull();
  expect(screen.queryByRole("heading")).toBeNull();
  await act(async () => release.get("/api/projects")!());
  await act(async () => release.get("/api/selectors")!());
  expect(screen.queryByRole("textbox")).toBeNull();
  await act(async () => release.get("/api/threads/configuration-preview")!());
  const editor = await screen.findByRole("textbox", { name: "Message" });
  expect(document.activeElement).toBe(editor);
  expect(screen.getByRole("heading").textContent).toContain("Example project");
  expect(
    screen.getByRole("combobox", { name: "Execution mode" }).textContent,
  ).toContain("Full Control");
  expect(screen.queryByText("Preparing your conversation…")).toBeNull();
  expect(writes).toHaveLength(0);
});

it("keeps the new draft sendable during background preview refresh and explains changed defaults", async () => {
  mount();
  await fill();
  const editor = screen.getByRole("textbox", { name: "Message" });
  let release!: () => void;
  const paused = new Promise<void>((resolve) => {
    release = resolve;
  });
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (request) => {
    if (
      new URL((request as Request).url).pathname.endsWith(
        "configuration-preview",
      )
    )
      await paused;
    return original(request);
  });
  act(() => {
    void queries.invalidateQueries({ queryKey: ["new-thread-preview"] });
  });
  expect(
    (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
  fireEvent.click(screen.getByRole("link", { name: "New in second project" }));
  await screen.findByText("Updating conversation settings…");
  expect(screen.getByRole("textbox", { name: "Message" })).toBe(editor);
  expect(screen.queryByText("Preparing your conversation…")).toBeNull();
  fireEvent.keyDown(editor, { key: "Enter" });
  expect(writes).toHaveLength(0);
  await act(async () => release());
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  fireEvent.keyDown(editor, { key: "Enter" });
  await waitFor(() => expect(writes).toHaveLength(2));
  expect((await writes[0].json()).defaults.project_id).toBe("project-two");
});

it("reveals setup errors instead of trapping a retained new draft behind loading", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (request) =>
    new URL((request as Request).url).pathname.endsWith("configuration-preview")
      ? json({ error: { message: "Choose an available agent" } }, 400)
      : original(request),
  );
  mount();
  const editor = await screen.findByRole("textbox", { name: "Message" });
  expect(screen.getByText("Choose an available agent")).toBeTruthy();
  expect(screen.getByRole("link", { name: "Continue setup" })).toBeTruthy();
  expect(screen.queryByText("Preparing your conversation…")).toBeNull();
  act(() => drafts.get(id)!.doc.getText("text").insert(0, "Keep this"));
  fireEvent.keyDown(editor, { key: "Enter" });
  expect(writes).toHaveLength(0);
  expect(values(drafts.get(id)!.doc).prompt).toBe("Keep this");
});

it("shows cached history before replay and draft sync while keeping Send guarded", async () => {
  let release!: () => void;
  focusPaused = new Promise<void>((resolve) => {
    release = resolve;
  });
  const connect = vi
    .mocked(ThreadDraft.prototype.connect)
    .getMockImplementation()!;
  let synchronize!: () => void;
  vi.mocked(ThreadDraft.prototype.connect).mockImplementation(function (
    this: ThreadDraft,
    ...args
  ) {
    synchronize = () => {
      connect.apply(this, args);
    };
    return { presence() {}, close() {} };
  });
  queries.setQueryData(["thread", id, "detail"], threadDetail);
  queries.setQueryData(["thread", id, "history", null], {
    pages: [
      {
        continuation_id: "initial:one",
        entries: [
          {
            position: 0,
            message_kind: "response",
            parts: [{ kind: "assistant", text: "Cached body" }],
          },
        ],
        next_cursor: null,
      },
    ],
    pageParams: [undefined],
  });
  mount(`/threads/${id}?compose=1`);
  expect(screen.getByText("Cached body")).toBeTruthy();
  expect(screen.queryByText("Opening conversation…")).toBeNull();
  const editor = await screen.findByRole("textbox", { name: "Shared prompt" });
  act(() => drafts.get(id)!.doc.getText("text").insert(0, "Waiting for sync"));
  fireEvent.keyDown(editor, { key: "Enter" });
  expect(writes).toHaveLength(0);
  expect(drafts.get(id)!.synchronized).toBe(false);
  act(() => drafts.get(id)!.doc.getText("text").delete(0, 16));
  await act(async () => {
    synchronize();
    release();
  });
  expect(document.activeElement).toBe(editor);
  act(() => drafts.get(id)!.doc.getText("text").insert(0, "Continue our work"));
  await waitFor(() => expect(drafts.get(id)!.synchronized).toBe(true));
  fireEvent.keyDown(editor, { key: "Enter" });
  await waitFor(() => expect(writes).toHaveLength(1));
  await waitFor(() => expect(values(drafts.get(id)!.doc).prompt).toBe(""));
  expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBe(editor);
  expect(screen.queryByText("Opening conversation…")).toBeNull();
  expect(screen.getByText("Continue our work")).toBeTruthy();
});

it("shows saved content with a reconnect notice when the initial live connection fails", async () => {
  focusUnavailable = true;
  mount(`/threads/${id}`);
  await screen.findByRole("textbox", { name: "Shared prompt" });
  expect(await screen.findByText(/Reconnecting live updates/)).toBeTruthy();
  expect(screen.queryByText("Opening conversation…")).toBeNull();
});

it.each([false, true])(
  "restores reading position before automatic pagination after delayed opening (saved: %s)",
  async (saved) => {
    if (saved)
      localStorage.setItem(
        `a13n-harness-ui.scroll.${id}`,
        JSON.stringify({ top: 430, follow: false }),
      );
    let releaseFocus!: () => void;
    let releaseHistory!: () => void;
    focusPaused = new Promise<void>((resolve) => {
      releaseFocus = resolve;
    });
    const delayedHistory = new Promise<void>((resolve) => {
      releaseHistory = resolve;
    });
    const historyRequests: string[] = [];
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (request) => {
      const url = new URL((request as Request).url);
      if (!url.pathname.endsWith("/transcript")) return original(request);
      historyRequests.push(url.search);
      await delayedHistory;
      return json({
        continuation_id: "initial:one",
        entries: [],
        next_cursor: "older",
      });
    });
    const view = mount(`/threads/${id}`);
    const reader = view.container.querySelector(
      '[class*="reading"]',
    )! as HTMLElement;
    let top = 0;
    Object.defineProperties(reader, {
      scrollHeight: { get: () => 2500 },
      clientHeight: { get: () => 500 },
      scrollTop: {
        get: () => top,
        set: (value: number) => {
          top = Math.max(0, Math.min(2000, value));
        },
      },
    });
    await waitFor(() => expect(historyRequests).toHaveLength(1));
    await act(async () => releaseHistory());
    await waitFor(() =>
      expect(
        queries.getQueryData(["thread", id, "history", null]),
      ).toBeTruthy(),
    );
    fireEvent.scroll(reader);
    expect(historyRequests).toHaveLength(1);
    expect(top).toBe(saved ? 430 : 2000);
    expect(screen.queryByText("Opening conversation…")).toBeNull();
    await act(async () => releaseFocus());
    await screen.findByRole("textbox", { name: "Shared prompt" });
    expect(top).toBe(saved ? 430 : 2000);
    expect(historyRequests).toHaveLength(1);
  },
);

it.each([
  [false, false],
  [true, false],
  [false, true],
])(
  "loads previous turns independently of complete turn loading (short viewport: %s, retry: %s)",
  async (short, retry) => {
    const requests: string[] = [];
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (request) => {
      const url = new URL((request as Request).url);
      if (!url.pathname.endsWith("/transcript")) return original(request);
      const cursor = url.searchParams.get("cursor") ?? "latest";
      if (url.searchParams.has("turn_id"))
        return json({
          continuation_id: "initial:one",
          entries: Array.from({ length: 97 }, (_, index) => ({
            position: index + 4,
            message_kind: index === 0 ? "request" : "response",
            parts:
              index === 0
                ? [{ kind: "user", text: "Current task" }]
                : index === 96
                  ? [{ kind: "assistant", text: "Current answer" }]
                  : index === 1
                    ? [{ kind: "thinking", text: "Folded intermediate work" }]
                    : [],
          })),
          next_cursor: "previous-turn",
        });
      requests.push(cursor);
      if (
        retry &&
        requests.filter((item) => item === "previous-turn").length === 1
      )
        return json(
          { error: { message: "History temporarily unavailable" } },
          503,
        );
      const earlier = cursor === "previous-turn";
      return json({
        continuation_id: "initial:one",
        entries: [
          {
            position: earlier ? 3 : cursor === "steps" ? 70 : 100,
            message_kind: "response",
            parts: [
              {
                kind: cursor === "steps" ? "reasoning" : "assistant",
                text: earlier
                  ? "Previous answer"
                  : cursor === "steps"
                    ? "Folded intermediate work"
                    : "Current answer",
              },
            ],
          },
        ],
        boundary_entries: [
          {
            position: earlier ? 2 : 4,
            message_kind: "request",
            parts: [
              {
                kind: "user",
                text: earlier ? "Previous task" : "Current task",
              },
            ],
          },
        ],
        turns: [
          {
            turn_id: earlier ? "previous" : "current",
            input_position: earlier ? 2 : 4,
            end_position: earlier ? 4 : 101,
            final_position: earlier ? 3 : 100,
            preview: earlier ? "Previous task" : "Current task",
            tool_count: earlier ? 0 : 50,
            steering_count: 0,
          },
        ],
        next_cursor: earlier ? "oldest" : "steps",
        earlier_turns_cursor: earlier ? "oldest" : "previous-turn",
        later_turns_cursor: null,
      });
    });
    const view = mount(`/threads/${id}`);
    const reader = view.container.querySelector(
      '[class*="reading"]',
    )! as HTMLElement;
    let top = 0;
    const height = () =>
      (short ? 300 : 1400) +
      (reader.textContent?.includes("Previous answer") ? 900 : 0);
    Object.defineProperties(reader, {
      clientHeight: { get: () => 600 },
      scrollHeight: { get: height },
      scrollTop: {
        get: () => top,
        set: (value: number) => {
          top = Math.max(0, Math.min(height() - 600, value));
        },
      },
    });
    await screen.findByText("Current answer");
    if (!short) {
      expect(requests).toEqual(["latest"]);
      fireEvent.wheel(reader, { deltaY: -100 });
      reader.scrollTop = 40;
      fireEvent.scroll(reader);
    }
    if (retry) {
      const retryButton = await screen.findByRole("button", {
        name: "Retry earlier messages",
      });
      fireEvent.scroll(reader);
      await act(async () => {});
      expect(requests).toEqual(["latest", "previous-turn"]);
      fireEvent.click(retryButton);
    }
    await screen.findByText("Previous answer");
    const expectedRequests = retry
      ? ["latest", "previous-turn", "previous-turn"]
      : ["latest", "previous-turn"];
    expect(requests).toEqual(expectedRequests);
    expect(top).toBe(short ? 600 : 940);
    expect(
      screen.queryByRole("button", {
        name: "Load earlier messages in this turn",
      }),
    ).toBeNull();
    const details = await screen.findByRole("button", {
      name: /Execution details/,
    });
    expect(details.getAttribute("aria-expanded")).toBe("false");
    expect(
      screen.getByText("Folded intermediate work").closest("[hidden]"),
    ).toBeTruthy();
    fireEvent.scroll(reader);
    await act(async () => {});
    expect(requests).toEqual(expectedRequests);
  },
);

it("automatically loads the whole visible turn when away from the top", async () => {
  const requests: string[] = [];
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (request) => {
    const url = new URL((request as Request).url);
    if (!url.pathname.endsWith("/transcript")) return original(request);
    requests.push(url.search);
    return json({
      continuation_id: "initial:one",
      entries: [
        ...(url.searchParams.has("cursor")
          ? Array.from({ length: 100 }, (_, position) => ({
              position,
              message_kind: "request",
              parts:
                position === 0 ? [{ kind: "user", text: "Long task" }] : [],
            }))
          : []),
        {
          position: 100,
          message_kind: "response",
          parts: [{ kind: "assistant", text: "Short final" }],
        },
      ],
      boundary_entries: [
        {
          position: 0,
          message_kind: "request",
          parts: [{ kind: "user", text: "Long task" }],
        },
      ],
      turns: [
        {
          turn_id: "long",
          input_position: 0,
          end_position: 101,
          final_position: 100,
          preview: "Long task",
          tool_count: 50,
          steering_count: 0,
        },
      ],
      next_cursor: url.searchParams.has("cursor") ? null : "older",
    });
  });
  const view = mount(`/threads/${id}`);
  const reader = view.container.querySelector(
    '[class*="reading"]',
  )! as HTMLElement;
  Object.defineProperties(reader, {
    clientHeight: { get: () => 600 },
    scrollHeight: { get: () => 1400 },
    scrollTop: { get: () => 800, set: () => {} },
  });
  await screen.findByText("Short final");
  await waitFor(() => expect(requests).toHaveLength(3));
  await waitFor(() => expect(screen.queryByText("Loading turn…")).toBeNull());
  fireEvent.scroll(reader);
  await act(async () => {});
  expect(requests).toHaveLength(3);
  expect(requests[1]).toContain("turn_id=long");
  expect(requests[2]).toContain("turn_id=long");
  expect(requests[2]).toContain("cursor=older");
  expect(
    screen.queryByRole("button", {
      name: "Load earlier messages in this turn",
    }),
  ).toBeNull();
});

it("returns to the latest window when New output is clicked from a historical input", async () => {
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (request) => {
    const url = new URL((request as Request).url);
    const turn = (old: boolean) => ({
      turn_id: old ? "old" : "latest",
      input_position: old ? 0 : 10,
      end_position: old ? 2 : 12,
      final_position: old ? 1 : 11,
      preview: old ? "Earlier task" : "Latest task",
      tool_count: 0,
      steering_count: 0,
    });
    if (url.pathname.endsWith("/inputs"))
      return json({
        continuation_id: "initial:one",
        turns: [turn(true), turn(false)],
      });
    if (!url.pathname.endsWith("/transcript")) return original(request);
    const old = url.searchParams.get("turn_id") === "old";
    return json({
      continuation_id: "initial:one",
      entries: [
        {
          position: old ? 0 : 10,
          message_kind: "request",
          parts: [{ kind: "user", text: old ? "Earlier task" : "Latest task" }],
        },
        {
          position: old ? 1 : 11,
          message_kind: "response",
          parts: [
            {
              kind: "assistant",
              text: old ? "Earlier answer" : "Latest answer",
            },
          ],
        },
      ],
      turns: [turn(old)],
      newer_cursor: old ? "later" : null,
    });
  });
  mount(`/threads/${id}`);
  await screen.findByText("Latest answer");
  fireEvent.click(
    await screen.findByRole("button", { name: "Input 1: Earlier task" }),
  );
  await screen.findByText("Earlier answer");
  expect(screen.queryByText("Latest answer")).toBeNull();
  // A refreshed checkpoint adds output while this historical window is open.
  await act(async () => {
    queries.setQueriesData<{ pages: Schema<"TranscriptPage">[] }>(
      { queryKey: ["thread", id, "history", null, "old"] },
      (current) =>
        current && {
          ...current,
          pages: current.pages.map((page) => ({
            ...page,
            entries: [
              ...page.entries,
              {
                position: 2,
                message_kind: "response",
                parts: [
                  { kind: "assistant", text: "Additional checkpoint output" },
                ],
              },
            ],
          })),
        },
    );
  });
  fireEvent.click(await screen.findByRole("button", { name: "New output" }));
  await screen.findByText("Latest answer");
  expect(screen.queryByText("Earlier answer")).toBeNull();
  expect(screen.queryByRole("button", { name: "Back to latest" })).toBeNull();
});

it.each(["new", "existing"])(
  "selects an environment for Send on a %s conversation without patching defaults",
  async (kind) => {
    mount(kind === "new" ? path : `/threads/${id}`);
    if (kind === "new") await fill();
    else {
      await screen.findByRole("button", { name: "Send" });
      act(() => drafts.get(id)!.doc.getText("text").insert(0, "Continue"));
    }
    const picker = await screen.findByRole("combobox", {
      name: "Execution mode",
    });
    await waitFor(() => expect(picker.hasAttribute("disabled")).toBe(false));
    const user = userEvent.setup();
    await user.click(picker);
    await user.click(await screen.findByRole("option", { name: /Sandbox/ }));
    expect(drafts.get(id)!.environment?.environment_profile_id).toBe(
      "environment-sandbox",
    );
    await waitFor(() => expect(picker.textContent).toContain("Sandbox"));
    expect(writes).toHaveLength(0);
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() =>
      expect(writes.some((request) => request.url.endsWith("/submit"))).toBe(
        true,
      ),
    );
    const submit = writes.find((request) => request.url.endsWith("/submit"))!;
    expect(await submit.json()).toMatchObject({
      environment: { environment_profile_id: "environment-sandbox" },
    });
    expect(writes.some((request) => request.method === "PATCH")).toBe(false);
    if (kind === "new") {
      const created = await writes
        .find((request) => new URL(request.url).pathname === "/api/threads")!
        .json();
      expect(created.defaults).not.toHaveProperty("environment_profile_id");
    }
  },
);

it.each(["desktop", "mobile"])(
  "loads complete %s turns automatically, reconciling steering and retrying failures",
  async (layout) => {
    if (layout === "mobile")
      vi.stubGlobal("matchMedia", (query: string) => ({
        matches: query === "(max-width: 700px)",
        addEventListener: vi.fn(),
        removeEventListener: vi.fn(),
      }));
    const requests: string[] = [];
    const steeringParts = previewInput("steer-one", ["Please use plan B"]);
    let failSteps = true;
    const original = vi.mocked(fetch).getMockImplementation()!;
    vi.mocked(fetch).mockImplementation(async (request) => {
      const url = new URL((request as Request).url);
      if (!url.pathname.endsWith("/transcript")) return original(request);
      const cursor = url.searchParams.get("cursor");
      const execution = url.searchParams.get("turn_id") === "current";
      requests.push(
        execution
          ? `execution:${cursor ?? "latest"}`
          : `conversation:${cursor ?? "latest"}`,
      );
      if (execution && cursor === "steps" && failSteps) {
        failSteps = false;
        return json(
          { error: { message: "Execution temporarily unavailable" } },
          503,
        );
      }
      const earlier = cursor === "previous-turn";
      return json({
        continuation_id: "initial:one",
        entries: earlier
          ? [
              {
                position: 0,
                message_kind: "request",
                parts: [{ kind: "user", text: "Previous prompt" }],
              },
              {
                position: 1,
                message_kind: "response",
                parts: [{ kind: "assistant", text: "Previous answer" }],
              },
            ]
          : execution && cursor === "steps"
            ? [
                {
                  position: 2,
                  message_kind: "request",
                  parts: [{ kind: "user", text: "Current prompt" }],
                },
                {
                  position: 3,
                  message_kind: "response",
                  parts: [{ kind: "thinking", text: "Earlier execution work" }],
                },
                { position: 4, message_kind: "request", parts: steeringParts },
                ...Array.from({ length: 75 }, (_, index) => ({
                  position: index + 5,
                  message_kind: "response",
                  parts:
                    index === 0
                      ? [{ kind: "assistant", text: "Earlier progress text" }]
                      : [],
                })),
              ]
            : [
                {
                  position: 80,
                  message_kind: "response",
                  parts: [{ kind: "thinking", text: "Recent execution work" }],
                },
                {
                  position: 102,
                  message_kind: "response",
                  parts: [{ kind: "assistant", text: "Current answer" }],
                },
                ...Array.from({ length: 21 }, (_, index) => ({
                  position: index + 81,
                  message_kind: "request",
                  parts: [],
                })),
              ],
        boundary_entries: earlier
          ? []
          : [
              {
                position: 2,
                message_kind: "request",
                parts: [{ kind: "user", text: "Current prompt" }],
              },
            ],
        turns: [
          {
            turn_id: earlier ? "previous" : "current",
            input_position: earlier ? 0 : 2,
            end_position: earlier ? 2 : 103,
            final_position: earlier ? 1 : 102,
            preview: earlier ? "Previous prompt" : "Current prompt",
          },
        ],
        next_cursor: earlier || cursor === "steps" ? null : "steps",
        earlier_turns_cursor: earlier ? null : "previous-turn",
        later_turns_cursor: null,
      });
    });
    const view = mount(`/threads/${id}`);
    const reader = view.container.querySelector(
      '[class*="reading"]',
    )! as HTMLElement;
    let top = 800;
    let liveGrowth = 0;
    Object.defineProperties(reader, {
      clientHeight: { get: () => 600 },
      scrollHeight: {
        get: () =>
          1400 +
          liveGrowth +
          (reader.textContent?.includes("Previous answer") ? 900 : 0),
      },
      scrollTop: {
        get: () => top,
        set: (value: number) => {
          top = value;
        },
      },
    });
    const retry = await screen.findByRole("button", {
      name: "Retry loading turn",
    });
    expect(requests).toEqual([
      "conversation:latest",
      "execution:latest",
      "execution:steps",
    ]);
    expect(
      screen.getByText("Current answer").closest("[data-execution-reader]"),
    ).toBeNull();
    // A failed complete-turn read cannot reveal a partial execution segment.
    expect(
      screen.queryByRole("button", { name: /Execution details/ }),
    ).toBeNull();
    await act(async () => {
      const draft = drafts.get(id)!;
      draft.localInputs = [
        {
          id: "steer-one",
          action: "steer",
          state: "accepted",
          parts: steeringParts,
        },
      ];
      draft.notify();
    });
    expect(screen.getAllByText("Please use plan B")).toHaveLength(1);
    // The outer gesture loads the previous prompt, not the incomplete turn's cursor.
    fireEvent.wheel(reader, { deltaY: -100 });
    reader.scrollTop = 40;
    fireEvent.scroll(reader);
    await screen.findByText("Previous answer");
    expect(requests.at(-1)).toBe("conversation:previous-turn");
    expect(screen.queryByText("Earlier execution work")).toBeNull();
    // One retry completes the turn without exposing transport-page controls.
    fireEvent.click(retry);
    await screen.findByText("Earlier progress text");
    expect(drafts.get(id)!.localInputs).toHaveLength(0);
    for (const text of [
      "Please use plan B",
      "Earlier progress text",
      "Current prompt",
      "Current answer",
    ]) {
      expect(screen.getAllByText(text)).toHaveLength(1);
      expect(
        screen.getByText(text).closest("[data-execution-reader]"),
      ).toBeNull();
      expect(screen.getByText(text).closest("[hidden]")).toBeNull();
    }
    expect(
      screen.queryByRole("button", {
        name: "Load earlier messages in this turn",
      }),
    ).toBeNull();
    expect(
      screen.queryByRole("region", { name: "Execution details" }),
    ).toBeNull();
    expect(requests).toEqual([
      "conversation:latest",
      "execution:latest",
      "execution:steps",
      "conversation:previous-turn",
      "execution:latest",
      "execution:steps",
    ]);
    const segments = screen.getAllByRole("button", {
      name: /Execution details/,
    });
    expect(segments).toHaveLength(2);
    fireEvent.click(segments[0]);
    const execution = await screen.findByRole("region", {
      name: "Execution details",
    });
    expect(within(execution).getByText("Earlier execution work")).toBeTruthy();
    expect(within(execution).queryByText("Recent execution work")).toBeNull();
    if (layout === "mobile") {
      const inspectionTop = reader.scrollTop;
      liveGrowth = 100;
      await act(async () => {
        queries.setQueriesData<{ pages: Schema<"TranscriptPage">[] }>(
          { queryKey: ["thread", id, "history"] },
          (current) =>
            current && {
              ...current,
              pages: current.pages.map((page) => ({
                ...page,
                completion_version: (page.completion_version ?? 0) + 1,
              })),
            },
        );
      });
      expect(reader.scrollTop).toBe(inspectionTop);
      fireEvent.click(
        screen.getByRole("button", { name: "Back to conversation" }),
      );
      await waitFor(() => expect(screen.queryByRole("dialog")).toBeNull());
    } else fireEvent.click(segments[0]);
    fireEvent.click(segments[0]);
    expect(
      await screen.findByRole("region", { name: "Execution details" }),
    ).toBe(execution);
    expect(requests).toHaveLength(6);
  },
);

it("keeps readable messages and an open execution reader through a multi-page checkpoint replacement", async () => {
  const input = {
    position: 0,
    message_kind: "request",
    parts: previewInput("round", ["Long task"]),
  };
  const progress = {
    position: 1,
    message_kind: "response",
    parts: [{ kind: "assistant", text: "Earlier progress" }],
  };
  const plan = {
    position: 2,
    message_kind: "response",
    parts: [{ kind: "thinking", text: "Inspect this plan" }],
  };
  const output = {
    position: 3,
    message_kind: "response",
    parts: [{ kind: "assistant", text: "Current progress" }],
  };
  const turn = {
    turn_id: "round",
    input_position: 0,
    end_position: 4,
    output_position: 3,
    preview: "Long task",
  };
  let finish!: () => void;
  const pausedTurn = new Promise<void>((resolve) => {
    finish = resolve;
  });
  let turnPages = 0;
  const original = vi.mocked(fetch).getMockImplementation()!;
  vi.mocked(fetch).mockImplementation(async (request) => {
    const url = new URL((request as Request).url);
    if (!url.pathname.endsWith("/transcript")) return original(request);
    const replacement =
      url.searchParams.get("expected_continuation_id") === "C1";
    const fullTurn = url.searchParams.has("turn_id");
    if (fullTurn) {
      turnPages++;
      if (url.searchParams.has("cursor")) {
        await pausedTurn;
        return json({ entries: [input, progress, plan], next_cursor: null });
      }
    }
    return json({
      continuation_id: replacement ? "C1" : null,
      entries: replacement ? [output] : [input, progress, plan, output],
      boundary_entries: [input],
      turns: [turn],
      next_cursor: replacement ? "older" : null,
      earlier_turns_cursor: null,
      later_turns_cursor: null,
    });
  });
  mount(`/threads/${id}`);
  const message = await screen.findByText("Earlier progress");
  fireEvent.click(screen.getByRole("button", { name: /Execution details/ }));
  const reader = screen.getByRole("region", { name: "Execution details" });
  await act(async () => {
    queries.setQueryData(["thread", id, "detail"], {
      ...threadDetail,
      continuation_id: "C1",
    });
  });
  await waitFor(() => expect(turnPages).toBe(2));
  expect(screen.queryByText("Loading turn…")).toBeNull();
  expect(screen.getByText("Earlier progress")).toBe(message);
  expect(screen.getByRole("region", { name: "Execution details" })).toBe(
    reader,
  );
  await act(async () => finish());
  await waitFor(() =>
    expect(queries.getQueryData(["thread", id, "history", "C1"])).toBeTruthy(),
  );
  expect(screen.queryByText("Loading turn…")).toBeNull();
  expect(screen.getByText("Earlier progress")).toBe(message);
  expect(screen.getByRole("region", { name: "Execution details" })).toBe(
    reader,
  );
  expect(
    screen
      .getByRole("button", { name: /Execution details/ })
      .getAttribute("aria-expanded"),
  ).toBe("true");
  expect(turnPages).toBe(2);
});

it("stages Coordinator locally, restores it and creates directly before Goal submission without confirmation", async () => {
  failure = "submit-reject";
  mount();
  await fill();
  const toggle = screen.getByRole("button", { name: "Coordinator" });
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-pressed")).toBe("true");
  fireEvent.click(toggle);
  expect(toggle.getAttribute("aria-pressed")).toBe("false");
  fireEvent.click(toggle);
  fireEvent.click(screen.getByRole("button", { name: "Goal" }));
  expect(writes).toHaveLength(0);
  expect(screen.queryByRole("dialog")).toBeNull();
  const restored = new NewDraftStore();
  expect(restored.get(new Map()).composer.coordinator).toBe(true);
  restored.dispose();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Conversation busy");
  expect(writes.map((request) => new URL(request.url).pathname)).toEqual([
    "/api/threads",
    `/api/threads/${id}/submit`,
  ]);
  expect(await writes[0].clone().json()).toMatchObject({
    thread_id: id,
    coordinator: true,
  });
  expect(await writes[1].clone().json()).toMatchObject({
    mode: "goal",
    parts: ["Build this"],
  });
  expect(screen.queryByRole("button", { name: "Coordinator" })).toBeNull();
  expect(screen.getByText("Coordinator")).toBeTruthy();
  expect(values(drafts.get(id)!.doc).prompt).toBe("Build this");
  failure = null;
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(new URL(writes[2].url).pathname).toBe(`/api/threads/${id}/submit`);
});

it("retains Coordinator intent across Project changes without falling back to ordinary submission", async () => {
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Coordinator" }));
  fireEvent.click(screen.getByRole("link", { name: "New without project" }));
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true),
  );
  expect(drafts.get(id)!.coordinator).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Coordinator" }));
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
        .disabled,
    ).toBe(false),
  );
  expect(writes).toHaveLength(0);
});

it("restores worker assignment and removes only ownership without losing the authored draft", async () => {
  mount(newConversationPath("project-one", ownerId));
  await fill();
  await screen.findByText("Managed by · Project manager");
  expect(screen.queryByRole("button", { name: "Coordinator" })).toBeNull();
  expect(
    (screen.getByRole("combobox", { name: "Project" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("button", { name: "Goal" }));
  const composer = drafts.get(id)!;
  act(() => {
    composer.modelId = "model-two";
    composer.notify();
  });
  const restored = new NewDraftStore();
  const retained = restored.get(new Map());
  expect(retained.coordinatorThreadId).toBe(ownerId);
  expect(retained.composer.coordinator).toBe(false);
  restored.dispose();
  fireEvent.click(
    screen.getByRole("button", { name: "Remove Coordinator assignment" }),
  );
  expect(values(composer.doc).prompt).toBe("Build this");
  expect(composer.modelId).toBe("model-two");
  expect(composer.mode).toBe("goal");
  expect(creations.current?.coordinatorThreadId).toBeUndefined();
  expect(
    screen
      .getByRole("button", { name: "Coordinator" })
      .getAttribute("aria-pressed"),
  ).toBe("false");
  expect(
    (screen.getByRole("combobox", { name: "Project" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
  expect(writes).toHaveLength(0);
});

it("creates an owned worker directly and keeps fixed ownership after a rejected first Goal submission", async () => {
  failure = "submit-reject";
  mount(newConversationPath("project-one", ownerId));
  await fill();
  await screen.findByText("Managed by · Project manager");
  fireEvent.click(screen.getByRole("button", { name: "Goal" }));
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Conversation busy");
  expect(await writes[0].clone().json()).toEqual({
    thread_id: id,
    defaults: { project_id: "project-one" },
    coordinator_thread_id: ownerId,
  });
  expect(await writes[1].clone().json()).toMatchObject({
    mode: "goal",
    parts: ["Build this"],
  });
  expect(
    screen.queryByRole("button", { name: "Remove Coordinator assignment" }),
  ).toBeNull();
  expect(screen.queryByRole("button", { name: "Coordinator" })).toBeNull();
  expect(screen.getByText("Managed by · Project manager")).toBeTruthy();
  failure = null;
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(writes).toHaveLength(3));
  expect(
    writes.filter(
      (request) => new URL(request.url).pathname === "/api/threads",
    ),
  ).toHaveLength(1);
});

it("locks uncertain worker creation, reconciles its retained identity and never resubmits automatically", async () => {
  failure = "create";
  mount(newConversationPath("project-one", ownerId));
  await fill();
  await screen.findByText("Managed by · Project manager");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText(
    "Unable to reach the server. Check your connection and try again.",
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Remove Coordinator assignment",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  fireEvent.click(screen.getByRole("link", { name: "New in second project" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Search").textContent).toContain(
      `coordinator=${ownerId}`,
    ),
  );
  expect(creations.current?.defaults.project_id).toBe("project-one");
  failure = null;
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
  expect(writes).toHaveLength(1);
  await screen.findByText("Managed by · Project manager");
  expect(
    screen.queryByRole("button", { name: "Remove Coordinator assignment" }),
  ).toBeNull();
});

it("blocks sending when owner refresh fails despite cached ownership", async () => {
  mount(newConversationPath("project-one", ownerId));
  await fill();
  await screen.findByText("Managed by · Project manager");
  const fetch = vi.mocked(globalThis.fetch);
  const original = fetch.getMockImplementation()!;
  fetch.mockImplementation(async (input, init) => {
    const request = input instanceof Request ? input : new Request(input, init);
    if (new URL(request.url).pathname === `/api/threads/${ownerId}`)
      return json({ error: { message: "Owner unavailable" } }, 500);
    return original(input, init);
  });
  await act(async () => {
    await queries.invalidateQueries({
      queryKey: ["thread", ownerId, "detail"],
    });
  });
  expect(queries.getQueryData(["thread", ownerId, "detail"])).toBeDefined();
  expect(queries.getQueryState(["thread", ownerId, "detail"])?.status).toBe(
    "error",
  );
  await waitFor(() =>
    expect(
      (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
        .disabled,
    ).toBe(true),
  );
  expect(writes).toHaveLength(0);
});

it("blocks unavailable owners rather than silently creating an independent thread", async () => {
  ownerArchived = true;
  mount(newConversationPath("project-one", ownerId));
  await fill();
  await screen.findByText("Managed by · Project manager");
  expect(
    (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  expect(writes).toHaveLength(0);
  fireEvent.click(
    screen.getByRole("button", { name: "Remove Coordinator assignment" }),
  );
  expect(
    (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
      .disabled,
  ).toBe(false);
});
