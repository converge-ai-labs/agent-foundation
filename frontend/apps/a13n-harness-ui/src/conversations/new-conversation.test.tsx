// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import * as Y from "yjs";
import { TransportContext } from "../transport/context";
import { createTransport } from "../transport/client";
import { ComposerDrafts } from "./composer";
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
let writes: Request[];
let reads: string[];
let failure: "create" | "submit" | "reject" | null;
let paused: Promise<void> | undefined;
let readPaused: Promise<void> | undefined;
let readFailure = false;
let historyPaused: Promise<void> | undefined;
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
  failure = null;
  paused = undefined;
  readPaused = undefined;
  readFailure = false;
  historyPaused = undefined;
  focused = undefined;
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
                name: "Local",
                mode: "full-control",
              },
            ],
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
          return json(detail);
        }
        if (pathname === `/api/threads/${id}/events`) {
          return new Response(
            new ReadableStream({
              start(controller) {
                if (focused)
                  controller.enqueue(
                    new TextEncoder().encode(
                      `data: ${JSON.stringify({ kind: "snapshot", snapshot: focused, resume_cursor: "cursor-one" })}\n\n`,
                    ),
                  );
                request.signal.addEventListener(
                  "abort",
                  () => controller.close(),
                  { once: true },
                );
              },
            }),
            { headers: { "Content-Type": "text/event-stream" } },
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
            agent_source: {
              id: (await request.clone().json()).agent_id || "agent-one",
            },
            environment_profile_id: "environment-native",
          },
        });
      writes.push(request.clone());
      if (pathname === "/api/threads") {
        await paused;
        if (failure === "create")
          throw new TypeError("Creation acknowledgement lost");
        if (failure === "reject")
          return json({ error: { message: "Configuration not ready" } }, 400);
        return json({ thread_id: id });
      }
      if (pathname.endsWith("/attachments"))
        return json({
          attachment_id: "attachment-one",
          name: "notes.txt",
          size: 5,
          media_type: "text/plain",
        });
      if (pathname.endsWith("/submit")) {
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

it("retains an uncertain submission without replaying it", async () => {
  failure = "submit";
  mount();
  await fill();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
  expect(drafts.get(id)!.submission.kind).toBe("unknown");
  expect(values(drafts.get(id)!.doc).prompt).toBe("Build this");
  expect(writes).toHaveLength(2);
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
    await screen.findByRole("option", { name: /Default · Writer/ }),
  ).toBeTruthy();
  expect(
    screen.getByRole("option", { name: /Writer.*agent-one/ }),
  ).toBeTruthy();
  await user.click(screen.getByRole("option", { name: /Reviewer.*agent-two/ }));
  await waitFor(() => expect(screen.queryByRole("listbox")).toBeNull());
  fireEvent.keyDown(screen.getByRole("combobox", { name: "Model" }), {
    key: "ArrowDown",
  });
  expect(
    (await screen.findByRole("option", { name: /Agent default/ })).textContent,
  ).toContain("Other model");
  await user.click(
    screen.getByRole("option", { name: /Primary model.*model-one/ }),
  );
  await user.click(screen.getByRole("combobox", { name: "Thinking" }));
  await user.click(
    await screen.findByRole("option", { name: /Quick thinking/ }),
  );
  await user.click(screen.getByRole("link", { name: "Settings" }));
  await user.click(screen.getByRole("link", { name: "Return to draft" }));
  expect(screen.getByRole("combobox", { name: "Model" }).textContent).toContain(
    "Primary model",
  );
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
    parts: ["Build this"],
    source_id: expect.stringMatching(/^input_[0-9a-f]{32}$/),
    model_id: "model-one",
    thinking: "low",
  });
});

it("opens immediately with retained input and a stable composer while detail and history load", async () => {
  let resume!: () => void;
  readPaused = new Promise<void>((resolve) => {
    resume = resolve;
  });
  mount();
  await fill();
  historyPaused = new Promise(() => {});
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(reads).toContain(`/api/threads/${id}/events`));
  const editor = screen.getByRole("textbox", { name: "Shared prompt" });
  const input = screen.getByText("Build this");
  expect(screen.getByLabelText("Location").textContent).toBe(`/threads/${id}`);
  expect(screen.queryByText(/Accepted|Preparing…|Sending…/)).toBeNull();
  expect(writes).toHaveLength(2);
  await act(async () => resume());
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
  expect(queries.getQueryData(["thread", id, "detail"])).toBeTruthy();
  await waitFor(() => expect(reads).toContain(`/api/threads/${id}/transcript`));
  expect(screen.getByText("Build this")).toBe(input);
  expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBe(editor);
  expect(screen.queryByText("Start something together.")).toBeNull();
  expect(writes).toHaveLength(2);
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
  const editor = screen.getByRole("textbox", { name: "Shared prompt" });
  const original = creations.current!;
  act(() => {
    original.composer.modelId = "model-two";
    original.composer.notify();
  });
  fireEvent.click(screen.getByRole("link", { name: "New in second project" }));
  await screen.findByRole("heading", {
    name: "What would you like to build in Second project?",
  });
  expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBe(editor);
  expect(editor.textContent).toBe("Build this");
  expect(creations.current).toBe(original);
  expect(original.defaults.project_id).toBe("project-two");
  expect(original.composer.modelId).toBe("model-two");
  fireEvent.click(screen.getByRole("link", { name: "Settings" }));
  fireEvent.click(screen.getByRole("link", { name: "Home" }));
  await screen.findByRole("heading", {
    name: "What would you like to build in Second project?",
  });
  expect(
    screen.getByRole("textbox", { name: "Shared prompt" }).textContent,
  ).toBe("Build this");
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
  expect(
    screen.getByRole("textbox", { name: "Shared prompt" }).textContent,
  ).toBe("Build this");
  expect(creations.current!.threadId).toBe(id);
  expect(screen.getByRole("combobox", { name: "Model" }).textContent).toContain(
    "Other model",
  );
  act(() => {
    const text = creations.current!.composer.doc.getText("text");
    text.delete(0, text.length);
  });
  reload("/");
  await screen.findByRole("textbox", { name: "Shared prompt" });
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
  await screen.findByRole("textbox", { name: "Shared prompt" });
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
  expect(
    screen.getByRole("textbox", { name: "Shared prompt" }).textContent,
  ).toBe("Build this");
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
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
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
    (await screen.findByRole("textbox", { name: "Shared prompt" })).textContent,
  ).toBe("Next task");
  expect(writes).toHaveLength(2);
});

it("preserves native deep-link parameters when restoring or changing Project", async () => {
  const user = (await import("@testing-library/user-event")).default.setup();
  mount("/?native=files&native_path=%2Ftmp%2Fnotes.txt&terminal=terminal-one");
  await screen.findByRole("textbox", { name: "Shared prompt" });
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
  historyPaused = new Promise(() => {});
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
    children: { executions: [], total: 0 },
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
