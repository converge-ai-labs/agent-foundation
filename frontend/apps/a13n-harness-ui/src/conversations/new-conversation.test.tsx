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
  type NewDraft,
} from "./new-conversation";

const id = `thread_${"a".repeat(32)}`;
const path = `/new/${id}?project=project-one`;
let writes: Request[];
let reads: string[];
let failure: "create" | "submit" | "reject" | null;
let paused: Promise<void> | undefined;
let readPaused: Promise<void> | undefined;
let drafts: Map<string, ThreadDraft>;
let creations: Map<string, NewDraft>;
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
  drafts = new Map();
  creations = new Map();
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
                route: "openai:primary",
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
          await readPaused;
          return json({ thread: { thread_id: id }, continuation_id: null });
        }
        if (pathname === `/api/threads/${id}/transcript`)
          return json({
            continuation_id: "initial:one",
            entries: [],
            next_cursor: null,
          });
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
  return <output aria-label="Location">{location.pathname}</output>;
}
function mount(initial = path) {
  return render(
    <QueryClientProvider client={queries}>
      <TransportContext value={createTransport("test", () => {})}>
        <ComposerDrafts value={drafts}>
          <NewConversationDrafts value={creations}>
            <MemoryRouter initialEntries={[initial]}>
              <Link to="/settings">Settings</Link>
              <Link to={initial}>Return to draft</Link>
              <Location />
              <Routes>
                <Route
                  path="/new/:draftId"
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
                <Route path="*" element={<p>Other page</p>} />
              </Routes>
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
  expect(reads.some((item) => item.startsWith("/api/threads/"))).toBe(false);
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
  expect(creations.get(id)!.attempted).toBe(false);
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

it("retains the home input across settings navigation without creating a conversation", async () => {
  mount("/");
  await screen.findByRole("textbox", { name: "Shared prompt" });
  const home = creations.get("@home")!;
  act(() =>
    drafts.get(home.threadId)!.doc.getText("text").insert(0, "Keep this"),
  );
  fireEvent.click(screen.getByRole("link", { name: "Settings" }));
  fireEvent.click(screen.getByRole("link", { name: "Return to draft" }));
  await screen.findByRole("textbox", { name: "Shared prompt" });
  expect(
    screen.getByRole("textbox", { name: "Shared prompt" }).textContent,
  ).toBe("Keep this");
  expect(writes).toHaveLength(0);
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
  expect(creations.get(id)!.created).toBe(true);
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
    model_id: "model-one",
  });
});

it("keeps the same composer mounted while the accepted conversation's first frame loads", async () => {
  let resume!: () => void;
  readPaused = new Promise<void>((resolve) => {
    resume = resolve;
  });
  mount();
  await fill();
  const editor = screen.getByRole("textbox", { name: "Shared prompt" });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(reads).toContain(`/api/threads/${id}`));
  expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBe(editor);
  expect(screen.getByLabelText("Location").textContent).toBe(`/new/${id}`);
  expect(writes).toHaveLength(2);
  await act(async () => resume());
  await waitFor(() =>
    expect(screen.getByLabelText("Location").textContent).toBe(
      `/threads/${id}`,
    ),
  );
  expect(queries.getQueryData(["thread", id, "detail"])).toBeTruthy();
  expect(queries.getQueryData(["thread", id, "history", null])).toBeTruthy();
  expect(writes).toHaveLength(2);
});
