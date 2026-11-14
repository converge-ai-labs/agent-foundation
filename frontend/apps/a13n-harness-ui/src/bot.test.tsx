// @vitest-environment jsdom
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import userEvent from "@testing-library/user-event";
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BotActions, BotComposerProvider, BotPage, BOT_THREAD_ID } from "./bot";
import { ConversationPage } from "./conversations/conversation";
import type { DecisionResponse } from "./conversations/decision-inputs";
import { createTransport, type Schema } from "./transport/client";
import { TransportContext } from "./transport/context";
import { ComposerDrafts } from "./conversations/composer";
import { ThreadDraft } from "./conversations/draft";
import * as Y from "yjs";
import { DraftContext, type SourceDraft } from "./configuration/drafts";
import { readDocument } from "./configuration/documents";
import { DebugProvider } from "./shell/debug";
import { recordAudio } from "./conversations/dictation-audio";
import conversationStyles from "./conversations/conversation.module.css";
import { MenuItem } from "a13n-ui";

vi.mock("./conversations/dictation-audio", () => ({
  recordAudio: vi.fn(),
  encodeAudio: async () => "recorded-audio",
}));

vi.mock("./conversations/live-threads", async () => {
  const { FocusDisplay } = await import("./conversations/stream");
  const snapshot = {
    display: new FocusDisplay(),
    connection: "Live",
    reconnections: 0,
    revision: 0,
  };
  return { useLiveThread: () => snapshot };
});

const tracker = vi.hoisted(() => ({
  observe: vi.fn(),
  acknowledge: vi.fn(async () => {}),
  invalidate: vi.fn(),
}));
vi.mock("./conversations/results", () => ({
  useResults: () => ({
    tracker,
    threads: new Map(
      snapshot?.thread
        ? [
            [
              BOT_THREAD_ID,
              {
                thread: {
                  ...snapshot.thread,
                  unread: true,
                  read_state_version: snapshot.thread.completion?.version ?? 0,
                },
              },
            ],
          ]
        : [],
    ),
  }),
}));

let snapshot: Schema<"BotSnapshot">;
let requests: Schema<"BotSubmitRequest">[];
let syncDraft: boolean;
function acknowledgeDraft() {
  draft.receive({
    kind: "draft",
    draft_id: "draft-bot",
    participant_id: "tester",
    participants: {},
    update_base64: btoa(
      String.fromCharCode(...Y.encodeStateAsUpdate(draft.doc)),
    ),
  });
}
let decisionResponses: DecisionResponse[];
const approval: Schema<"ApprovalRequestView"> = {
  kind: "approval",
  request_id: "approval-one",
  tool_name: "create_project",
  arguments: { name: "Reviewed project" },
  override_allowed: false,
};
let reject: boolean;
let sendGate: Promise<void> | undefined;
let rejectionStatus: number | undefined;
let uploadFailure: boolean;
let botReadGate: Promise<void> | undefined;
let historyGate: Promise<void> | undefined;
let historyFailure: boolean;
let historyPage: Schema<"TranscriptPage">;
let earlierHistoryFailure: boolean;
let detailMissing: boolean;
let rootSource: string;
let configurationWrites: string[];
let writable: boolean;
let saveFailure: "validation" | "unknown" | null;
let saveGate: Promise<void> | undefined;
let savedDrafts: Map<string, SourceDraft>;
let queries: QueryClient;
let transport: ReturnType<typeof createTransport>;
let voiceResponse: Promise<string> | undefined;
let voiceRequests: unknown[];
let draft: ThreadDraft;
const profile = { display_name: "Tester", color: "#666666" };
const attachment: Schema<"ThreadAttachment"> = {
  attachment_id: "attachment-example",
  name: "notes.txt",
  media_type: "text/plain",
  size: 5,
};
function typeMessage(text: string) {
  act(() => {
    const input = draft.doc.getText("text");
    input.delete(0, input.length);
    input.insert(0, text);
  });
}
beforeEach(() => {
  vi.clearAllMocks();
  localStorage.removeItem("a13n-harness-ui.debug");
  draft = new ThreadDraft();
  syncDraft = true;
  vi.spyOn(draft, "connect").mockImplementation(() => {
    acknowledgeDraft();
    const acknowledge = () => {
      if (syncDraft) queueMicrotask(acknowledgeDraft);
    };
    draft.doc.on("update", acknowledge);
    return {
      presence: vi.fn(),
      close: () => draft.doc.off("update", acknowledge),
    };
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
    value: () => new DOMRect(),
  });
  snapshot = {
    thread_id: BOT_THREAD_ID,
    state: { activated: false, paused: false },
    available: true,
    enabled: true,
    heartbeat_enabled: true,
    interval_seconds: 3600,
    messages: [],
    inputs: [],
  };
  requests = [];
  voiceRequests = [];
  voiceResponse = undefined;
  decisionResponses = [];
  rootSource =
    '# Keep this comment\nschema_version: "1"\ndefaults: {agent: agent-assistant}\nwebui:\n  bot: {enabled: false}\n';
  configurationWrites = [];
  writable = true;
  saveFailure = null;
  saveGate = undefined;
  savedDrafts = new Map();
  reject = false;
  sendGate = undefined;
  rejectionStatus = undefined;
  uploadFailure = false;
  botReadGate = undefined;
  historyGate = undefined;
  historyFailure = false;
  historyPage = { entries: [], turns: [], total: 0 };
  earlierHistoryFailure = false;
  detailMissing = false;
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
      if (path === "/api/dictation") {
        voiceRequests.push(await request.json());
        return Response.json({ text: await voiceResponse });
      }
      if (path === "/api/bot/inputs") {
        requests.push(await request.json());
        await sendGate;
        if (reject) throw new TypeError("Offline");
        if (rejectionStatus)
          return Response.json(
            { error: { message: "Not accepting input" } },
            { status: rejectionStatus },
          );
        const command = requests.at(-1)!;
        if (command.draft) {
          const captured = new Y.Doc({ gc: false });
          Y.applyUpdate(
            captured,
            Uint8Array.from(atob(command.draft.update_base64), (c) =>
              c.charCodeAt(0),
            ),
          );
          captured.getText("text").delete(0, captured.getText("text").length);
          Y.applyUpdate(draft.doc, Y.encodeStateAsUpdate(captured));
          captured.destroy();
          acknowledgeDraft();
        }
        snapshot.messages = [
          ...(snapshot.messages ?? []).filter(
            (message) => message.message_id !== `user-${command.input_id}`,
          ),
          {
            message_id: `user-${command.input_id}`,
            role: "user",
            text: command.text ?? "",
            parts: command.parts?.map((part) =>
              typeof part === "string" ? part : attachment,
            ),
            created_at: "2026-11-12T00:00:00Z",
          },
        ];
        return Response.json({
          input_id: command.input_id,
          text: command.text,
          parts: command.parts?.map((part) =>
            typeof part === "string" ? part : attachment,
          ),
          source: "user",
          created_at: "2026-11-12T00:00:00Z",
          state: "admitted",
        });
      }
      if (path === `/api/threads/${BOT_THREAD_ID}/attachments`) {
        if (uploadFailure)
          return Response.json({ message: "Upload failed" }, { status: 400 });
        return Response.json(attachment);
      }
      if (path.endsWith(`/attachments/${attachment.attachment_id}/metadata`))
        return Response.json(attachment);
      if (path.endsWith("/transcript")) {
        await historyGate;
        const earlier = new URL(request.url).searchParams.has("cursor");
        if (historyFailure || (earlier && earlierHistoryFailure))
          return Response.json(
            { error: { message: "History unavailable" } },
            { status: 503 },
          );
        return Response.json(
          earlier ? { entries: [], turns: [], total: 0 } : historyPage,
        );
      }
      if (path === "/api/bot" && botReadGate) await botReadGate;
      if (path === "/api/bot/pause") {
        snapshot = {
          ...snapshot,
          state: { ...snapshot.state, ...(await request.json()) },
        };
      }
      if (
        path === `/api/threads/${BOT_THREAD_ID}` ||
        path === "/api/threads/thread-ordinary"
      ) {
        if (detailMissing)
          return Response.json(
            { error: { message: "Chat not found" } },
            { status: 404 },
          );
        return Response.json({
          thread: {
            root_activity: { state: "inactive" },
            configuration: {
              version: 1,
              agent_source: { kind: "bot", id: "bot" },
              local_roots: [],
            },
            ...snapshot.thread,
          },
          continuation_id: "bot-continuation",
          available_actions: [],
          deferred_requests: snapshot.pending_decision ? [approval] : [],
        });
      }
      if (path === `/api/threads/${BOT_THREAD_ID}/decisions`) {
        if (request.method === "POST") {
          const body = await request.json();
          expect(body.expected_continuation_id).toBe("bot-continuation");
          decisionResponses.push(...body.responses);
          snapshot = { ...snapshot, pending_decision: false };
          return Response.json({ receipt_id: "receipt-decision" });
        }
        return Response.json({
          continuation_id: "bot-continuation",
          requests: [approval],
        });
      }
      if (path === "/api/selectors")
        return Response.json({
          dictation: { context_words: 100 },
          agents: [
            {
              agent_id: "agent-assistant",
              model_id: "model-primary",
              name: "Assistant",
            },
            {
              agent_id: "agent-coordinator",
              name: "Coordinator",
              model_id: "model-alternate",
            },
            { agent_id: "agent-no-model", name: "No model", model_id: null },
          ],
          models: [
            {
              model_id: "model-primary",
              name: "Primary",
              route: "openai:gpt-5",
            },
            {
              model_id: "model-alternate",
              name: "Alternate",
              route: "openai:alternate",
            },
          ],
        });
      if (path === "/api/configuration/sources")
        return Response.json({
          sources: [
            {
              resource_kind: "root",
              relative_path: "root.yaml",
              content_available: true,
              writable,
            },
          ],
        });
      if (path === "/api/configuration/sources/root.yaml") {
        if (request.method === "PUT") {
          if (saveFailure === "unknown") throw new TypeError("Offline");
          if (saveFailure === "validation")
            return Response.json(
              { error: { message: "Invalid configuration" } },
              { status: 422 },
            );
          const submitted = (await request.json()).content;
          configurationWrites.push(submitted);
          await saveGate;
          rootSource = submitted;
          snapshot = { ...snapshot, enabled: true };
          return Response.json({ source_digest: "saved" });
        }
        return Response.json({ content: rootSource, writable });
      }
      if (path === "/api/bot") return Response.json(snapshot);
      return Response.json(
        { message: "Not provided by this fixture" },
        { status: 404 },
      );
    }),
  );
  queries = new QueryClient({ defaultOptions: { queries: { retry: false } } });
  transport = createTransport("test", () => {});
});
afterEach(() => {
  cleanup();
  transport.close();
  draft.doc.destroy();
  queries.clear();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
function CurrentLocation() {
  const location = useLocation();
  return (
    <output data-testid="location">
      {location.pathname}
      {location.search}
    </output>
  );
}
function mount(initial = "/bot", withActions = false) {
  return render(
    <DebugProvider>
      <TransportContext value={transport}>
        <QueryClientProvider client={queries}>
          <DraftContext value={savedDrafts}>
            <ComposerDrafts
              value={
                new Map([
                  [BOT_THREAD_ID, draft],
                  ["thread-ordinary", draft],
                ])
              }
            >
              <MemoryRouter initialEntries={[initial]}>
                <BotComposerProvider>
                  <Link to="/elsewhere">Leave</Link>
                  <Link to="/bot">Return</Link>
                  <CurrentLocation />
                  <Routes>
                    <Route
                      path="/bot"
                      element={
                        <>
                          {withActions && <BotActions />}
                          <BotPage profile={profile} unauthorized={() => {}} />
                        </>
                      }
                    />
                    <Route
                      path="/threads/:threadId"
                      element={
                        <ConversationPage
                          profile={profile}
                          unauthorized={() => {}}
                        />
                      }
                    />
                    <Route path="/elsewhere" element={<p>Elsewhere</p>} />
                  </Routes>
                </BotComposerProvider>
              </MemoryRouter>
            </ComposerDrafts>
          </DraftContext>
        </QueryClientProvider>
      </TransportContext>
    </DebugProvider>,
  );
}
it("waits for shared acknowledgement and clears only captured edits after admission", async () => {
  syncDraft = false;
  let release!: () => void;
  sendGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  typeMessage("Captured input");
  expect(draft.synchronized).toBe(false);
  const send = screen.getByRole("button", { name: "Send" });
  fireEvent.click(send);
  fireEvent.click(send);
  await act(async () => {
    await Promise.resolve();
  });
  expect(requests).toHaveLength(0);
  act(acknowledgeDraft);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].draft?.draft_id).toBe("draft-bot");
  expect(requests[0].text).toBe("Captured input");
  act(() =>
    draft.doc
      .getText("text")
      .insert(draft.doc.getText("text").length, " Peer next input"),
  );
  await act(async () => {
    release();
  });
  await waitFor(() =>
    expect(draft.doc.getText("text").toString()).toBe(" Peer next input"),
  );
  expect(draft.connect).toHaveBeenCalledWith(
    transport,
    BOT_THREAD_ID,
    expect.any(Function),
  );
});

it("submits to the lazy root through the ordinary shared draft and clears accepted input", async () => {
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  typeMessage("Track this");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Send" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  expect(screen.queryByText("Ready when you are")).toBeNull();
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  await waitFor(() => expect(draft.doc.getText("text").toString()).toBe(""));
});
it("submits human input while busy or paused through Bot admission only", async () => {
  snapshot = {
    ...snapshot,
    state: { activated: true, paused: true },
    operation: {
      receipt: {
        receipt_id: "receipt-existing",
        thread_id: BOT_THREAD_ID,
        submitted_at: "2026-11-10T00:00:00Z",
      },
      status: "running",
    },
  };
  mount("/bot", true);
  await screen.findByText("Automation paused");
  expect(screen.getByText("Working…")).toBeTruthy();
  typeMessage("Change direction now");
  fireEvent.click(screen.getByRole("button", { name: "Steer" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  const paths = vi
    .mocked(fetch)
    .mock.calls.map(([request]) => new URL((request as Request).url).pathname);
  expect(paths.some((path) => /\/(submit|steer)$/.test(path))).toBe(false);
});

it("retains drafts and uncertain exact commands across navigation without automatic retry", async () => {
  reject = true;
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  typeMessage("Do once");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Send" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  fireEvent.click(screen.getByText("Leave"));
  fireEvent.click(screen.getByText("Return"));
  expect(
    screen.getByRole("textbox", { name: "Shared prompt" }).textContent,
  ).toBe("Do once");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByRole("button", { name: "Retry exactly" });
  expect(requests).toHaveLength(1);
  fireEvent.click(screen.getByText("Leave"));
  fireEvent.click(screen.getByText("Return"));
  expect(
    screen.getByRole("textbox", { name: "Shared prompt" }).closest("[inert]"),
  ).not.toBeNull();
  reject = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry exactly" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toEqual(requests[0]);
});
it("acknowledges the rendered completion, preserves a scrolled-up reader, and exposes human decisions", async () => {
  snapshot = {
    ...snapshot,
    thread: {
      thread_id: BOT_THREAD_ID,
      completion: { version: 3 },
    } as Schema<"ThreadSummary">,
    messages: [
      {
        message_id: "m1",
        role: "assistant",
        text: "Verified result",
        created_at: "2026-11-10T00:00:00Z",
      },
    ],
  };
  const view = mount();
  await screen.findByText("Verified result");
  const visibleReader = view.container.querySelector<HTMLElement>(
    "[class*='reading_']",
  )!;
  Object.defineProperty(visibleReader, "clientHeight", {
    configurable: true,
    value: 200,
  });
  vi.spyOn(visibleReader, "getClientRects").mockReturnValue([
    new DOMRect(0, 0, 300, 200),
  ] as unknown as DOMRectList);
  vi.spyOn(visibleReader, "getBoundingClientRect").mockReturnValue(
    new DOMRect(0, 0, 300, 200),
  );
  fireEvent.scroll(visibleReader);
  await waitFor(() =>
    expect(tracker.acknowledge).toHaveBeenCalledWith(BOT_THREAD_ID, 3, 3),
  );
  tracker.acknowledge.mockClear();
  const reader = view.container.querySelector<HTMLElement>(
    "[class*='reading_']",
  )!;
  Object.defineProperties(reader, {
    scrollHeight: { configurable: true, value: 1000 },
    clientHeight: { configurable: true, value: 200 },
    scrollTop: { configurable: true, value: 100, writable: true },
  });
  fireEvent.wheel(reader, { deltaY: -1 });
  fireEvent.scroll(reader);
  snapshot = {
    ...snapshot,
    pending_decision: true,
    thread: {
      ...snapshot.thread!,
      completion: { version: 4 },
    } as Schema<"ThreadSummary">,
  };
  await act(async () => {
    queries.setQueryData(["bot"], snapshot);
    await queries.invalidateQueries({
      queryKey: ["thread", BOT_THREAD_ID, "detail"],
    });
  });
  expect(reader.scrollTop).toBe(100);
  expect(tracker.acknowledge).not.toHaveBeenCalled();
  expect(
    await screen.findByRole("region", { name: "Pending decisions" }),
  ).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Activity" })).toBeNull();
  expect(
    (screen.getByRole("button", { name: "Send" }) as HTMLButtonElement)
      .disabled,
  ).toBe(true);
  reader.scrollTop = 800;
  fireEvent.scroll(reader);
  expect(tracker.acknowledge).toHaveBeenCalledWith(BOT_THREAD_ID, 4, 4);
});

it("discloses Host introduction and enables with one policy save without a duplicate browser submission", async () => {
  snapshot.enabled = false;
  mount();
  await screen.findByText(/Turning on sends one visible first message/);
  const enable = await screen.findByRole("button", { name: "Turn on Bot" });
  await waitFor(() => expect(enable.hasAttribute("disabled")).toBe(false));
  expect(
    screen.getByRole("combobox", { name: "Bot model" }).textContent,
  ).toContain("Primary");
  expect(screen.queryByRole("button", { name: "Send" })).toBeNull();
  fireEvent.change(
    screen.getByRole("textbox", { name: "Custom instructions" }),
    { target: { value: "Track release progress" } },
  );
  fireEvent.click(enable);
  await screen.findByRole("button", { name: "Send" });
  expect(configurationWrites).toHaveLength(1);
  expect(configurationWrites[0]).toContain("# Keep this comment");
  expect(configurationWrites[0]).toContain("Track release progress");
  // The Host owns the one-time submission; the browser only publishes policy.
  expect(requests).toHaveLength(0);
});

it.each([false, true])(
  "preserves concurrently saved, untouched Bot fields and YAML nodes when setup edits are %s",
  async (editInstructions) => {
    snapshot.enabled = false;
    rootSource = `# Keep this comment
schema_version: "1"
defaults: {agent: agent-assistant}
webui:
  bot:
    enabled: false
    instructions: Original
    interval_seconds: 3600
    connector_interval_seconds: 180
`;
    mount();
    const enable = await screen.findByRole("button", { name: "Turn on Bot" });
    await waitFor(() => expect(enable.hasAttribute("disabled")).toBe(false));
    if (editInstructions)
      fireEvent.change(
        screen.getByRole("textbox", { name: "Custom instructions" }),
        { target: { value: "Track release progress" } },
      );
    // Another tab/source edit is accepted after setup opened.
    rootSource = rootSource
      .replace("instructions: Original", "instructions: Changed elsewhere")
      .replace(
        "interval_seconds: 3600",
        "interval_seconds: 7200 # Latest interval",
      )
      .replace(
        "connector_interval_seconds: 180",
        "connector_interval_seconds: 600 # Latest delivery interval",
      );
    fireEvent.click(enable);
    await screen.findByRole("button", { name: "Send" });
    expect(configurationWrites).toHaveLength(1);
    const saved = readDocument(configurationWrites[0])!;
    expect(saved.getIn(["webui", "bot", "enabled"])).toBe(true);
    expect(saved.getIn(["webui", "bot", "instructions"])).toBe(
      editInstructions ? "Track release progress" : "Changed elsewhere",
    );
    expect(saved.getIn(["webui", "bot", "interval_seconds"])).toBe(7200);
    expect(saved.getIn(["webui", "bot", "connector_interval_seconds"])).toBe(
      600,
    );
    expect(configurationWrites[0]).toContain("# Keep this comment");
    expect(configurationWrites[0]).toContain("# Latest interval");
    expect(configurationWrites[0]).toContain("# Latest delivery interval");
    expect(requests).toHaveLength(0);
  },
);

it("keeps used off Bot history read-only with settings recovery and no Notes management", async () => {
  snapshot.enabled = false;
  snapshot.messages = [
    {
      message_id: "saved",
      role: "assistant",
      text: "Verified saved result",
      created_at: "2026-11-10T00:00:00Z",
    },
  ];
  mount();
  await screen.findByText("Verified saved result");
  expect(screen.queryByRole("textbox", { name: "Shared prompt" })).toBeNull();
  expect(
    screen.getByRole("link", { name: "Bot settings" }).getAttribute("href"),
  ).toBe("/settings#bot");
  expect(screen.queryByText("Registered work")).toBeNull();
});

it("keeps read-only setup disabled and protects unsaved global settings", async () => {
  snapshot.enabled = false;
  writable = false;
  mount();
  const enable = await screen.findByRole("button", { name: "Turn on Bot" });
  await screen.findByRole("combobox", { name: "Bot model" });
  expect(enable.hasAttribute("disabled")).toBe(true);
  cleanup();
  writable = true;
  queries.clear();
  savedDrafts.set("root.yaml", {
    content: "unsaved",
    base: rootSource,
    digest: "base",
    replacement: false,
  });
  mount();
  const editable = await screen.findByRole("button", { name: "Turn on Bot" });
  await waitFor(() => expect(editable.hasAttribute("disabled")).toBe(false));
  fireEvent.click(editable);
  await screen.findByText(
    "Save or discard your global settings changes first.",
  );
  expect(configurationWrites).toHaveLength(0);
  expect(savedDrafts.get("root.yaml")?.content).toBe("unsaved");
});

it("retains setup edits after validation failure and rechecks unknown saves before retry", async () => {
  snapshot.enabled = false;
  saveFailure = "validation";
  mount();
  const enable = await screen.findByRole("button", { name: "Turn on Bot" });
  await waitFor(() => expect(enable.hasAttribute("disabled")).toBe(false));
  fireEvent.change(
    screen.getByRole("textbox", { name: "Custom instructions" }),
    { target: { value: "Keep this draft" } },
  );
  fireEvent.click(enable);
  await screen.findByText("Invalid configuration");
  expect(
    (
      screen.getByRole("textbox", {
        name: "Custom instructions",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("Keep this draft");
  saveFailure = "unknown";
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  const check = await screen.findByRole("button", { name: "Check again" });
  expect(screen.queryByRole("button", { name: "Retry" })).toBeNull();
  expect(configurationWrites).toHaveLength(0);
  // The server accepted the policy even though its response was lost.
  snapshot.enabled = true;
  fireEvent.click(check);
  await screen.findByRole("button", { name: "Send" });
  expect(configurationWrites).toHaveLength(0);
  expect(requests).toHaveLength(0);
});

it("keeps setup edits across navigation and describes Heartbeat-off accurately", async () => {
  snapshot.enabled = false;
  mount();
  const instructions = await screen.findByRole("textbox", {
    name: "Custom instructions",
  });
  fireEvent.change(instructions, { target: { value: "Retain my setup" } });
  fireEvent.click(screen.getByRole("switch", { name: "Heartbeat" }));
  expect(
    screen.getByText(/Heartbeat is off; you can chat anytime/),
  ).toBeTruthy();
  fireEvent.click(screen.getByText("Leave"));
  fireEvent.click(screen.getByText("Return"));
  expect(
    (
      (await screen.findByRole("textbox", {
        name: "Custom instructions",
      })) as HTMLTextAreaElement
    ).value,
  ).toBe("Retain my setup");
  expect(
    screen
      .getByRole("switch", { name: "Heartbeat" })
      .getAttribute("aria-checked"),
  ).toBe("false");
});

it("freezes all setup fields and retains the save across navigation while publication is pending", async () => {
  snapshot.enabled = false;
  let release!: () => void;
  saveGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  mount();
  const enable = await screen.findByRole("button", { name: "Turn on Bot" });
  await waitFor(() => expect(enable.hasAttribute("disabled")).toBe(false));
  fireEvent.change(
    screen.getByRole("textbox", { name: "Custom instructions" }),
    { target: { value: "Submitted A" } },
  );
  fireEvent.click(enable);
  await waitFor(() => expect(configurationWrites).toHaveLength(1));
  for (const name of [
    "Custom instructions",
    "Bot model",
    "Heartbeat",
    "Check in every",
  ]) {
    const control = screen.getByRole(
      name === "Custom instructions"
        ? "textbox"
        : name === "Heartbeat"
          ? "switch"
          : "combobox",
      { name },
    );
    expect(control.closest("fieldset")?.disabled).toBe(true);
  }
  fireEvent.change(
    screen.getByRole("textbox", { name: "Custom instructions" }),
    { target: { value: "Attempted B" } },
  );
  fireEvent.click(screen.getByText("Leave"));
  fireEvent.click(screen.getByText("Return"));
  expect(
    screen
      .getByRole("button", { name: "Turn on Bot" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(
    (
      screen.getByRole("textbox", {
        name: "Custom instructions",
      }) as HTMLTextAreaElement
    ).value,
  ).toBe("Submitted A");
  await act(async () => {
    release();
  });
  await screen.findByRole("button", { name: "Send" });
  expect(configurationWrites).toHaveLength(1);
  expect(configurationWrites[0]).toContain("Submitted A");
  expect(configurationWrites[0]).not.toContain("Attempted B");
});

it("preserves a newer Settings draft when setup publication finishes after route unmount", async () => {
  snapshot.enabled = false;
  let release!: () => void;
  saveGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  mount();
  const enable = await screen.findByRole("button", { name: "Turn on Bot" });
  await waitFor(() => expect(enable.hasAttribute("disabled")).toBe(false));
  fireEvent.click(enable);
  await waitFor(() => expect(configurationWrites).toHaveLength(1));
  fireEvent.click(screen.getByText("Leave"));
  const newer = rootSource + "# New unsaved Settings edit\n";
  savedDrafts.set("root.yaml", {
    content: newer,
    base: rootSource,
    digest: "old",
    replacement: false,
  });
  await act(async () => {
    release();
  });
  fireEvent.click(screen.getByText("Return"));
  await screen.findByRole("button", { name: "Send" });
  // Retain the original conflict metadata: this older document does not include
  // Bot activation, so reopening Settings must warn about the external change.
  expect(savedDrafts.get("root.yaml")).toEqual({
    content: newer,
    base: newer.replace("# New unsaved Settings edit\n", ""),
    digest: "old",
    replacement: false,
  });
  expect(savedDrafts.get("root.yaml")?.content).not.toBe(
    savedDrafts.get("root.yaml")?.base,
  );
});

it("offers Settings recovery and keeps busy chat available without queue warnings", async () => {
  snapshot.available = false;
  snapshot.model_id = null;
  mount();
  await screen.findByText(
    "Check Bot's model in Settings. Another app may also own Bot processing.",
  );
  expect(
    screen.getByRole("link", { name: "Bot settings" }).getAttribute("href"),
  ).toBe("/settings#bot");
  snapshot = {
    ...snapshot,
    available: true,
    inputs: [
      {
        input_id: "pending",
        source: "user",
        text: "existing",
        created_at: "2026-11-11T00:00:00Z",
        state: "admitted",
      },
    ],
  };
  await act(async () => {
    queries.setQueryData(["bot"], snapshot);
  });
  const working = await screen.findByText("Preparing…");
  expect(working.closest("[role='status']")?.querySelector("svg")).toBeTruthy();
  expect(working.closest("[class*='reading_']")).toBeTruthy();
  expect(screen.getAllByText("Preparing…")).toHaveLength(1);
  expect(screen.queryByText(/will be queued/)).toBeNull();
  expect(screen.queryByRole("link", { name: "Model" })).toBeNull();
  typeMessage("Change direction now");
  expect(
    screen.getByRole("button", { name: "Send" }).hasAttribute("disabled"),
  ).toBe(false);
});

it("redirects the reserved Thread URL to Bot chat with native history and without sharing", async () => {
  snapshot.thread = { thread_id: BOT_THREAD_ID } as Schema<"ThreadSummary">;
  snapshot.messages = [
    {
      message_id: "result",
      role: "assistant",
      text: "Published result",
      created_at: "2026-11-11T00:00:00Z",
    },
  ];
  mount(`/threads/${BOT_THREAD_ID}?dialog=share&native=files`, true);
  await screen.findByText("Published result");
  expect(screen.getByTestId("location").textContent).toBe(
    "/bot?dialog=share&native=files",
  );
  expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  expect(screen.getByRole("link", { name: "Bot settings" })).toBeTruthy();
  expect(screen.queryByRole("link", { name: "Activity" })).toBeNull();
  await waitFor(() =>
    expect(
      vi
        .mocked(fetch)
        .mock.calls.some(
          ([request]) =>
            new URL((request as Request).url).pathname ===
            `/api/threads/${BOT_THREAD_ID}`,
        ),
    ).toBe(true),
  );
  expect(screen.queryByRole("button", { name: "Share" })).toBeNull();
  expect(screen.queryByRole("dialog", { name: "Share" })).toBeNull();
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(
        ([request]) =>
          new URL((request as Request).url).pathname === "/api/shares",
      ),
  ).toBe(false);
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([request]) =>
        new URL((request as Request).url).pathname.endsWith("/transcript"),
      ),
  ).toBe(true);
});

it("answers Bot decisions inside chat and refreshes the composer without a Thread run", async () => {
  snapshot = {
    ...snapshot,
    pending_decision: true,
    thread: { thread_id: BOT_THREAD_ID } as Schema<"ThreadSummary">,
  };
  mount();
  fireEvent.click(await screen.findByRole("button", { name: "Approve once" }));
  await waitFor(() =>
    expect(decisionResponses).toEqual([
      { kind: "approval", request_id: "approval-one", approved: true },
    ]),
  );
  await waitFor(() =>
    expect(
      screen.queryByRole("region", { name: "Pending decisions" }),
    ).toBeNull(),
  );
  typeMessage("Continue the work");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].text).toBe("Continue the work");
  expect(screen.getByTestId("location").textContent).toBe("/bot");
  expect(
    vi
      .mocked(fetch)
      .mock.calls.some(([request]) =>
        /\/(run|steer)$/.test(new URL((request as Request).url).pathname),
      ),
  ).toBe(false);
});

it("asks for an existing model rather than adding one when no default is configured", async () => {
  snapshot.enabled = false;
  rootSource = 'schema_version: "1"\nwebui:\n  bot: {enabled: false}\n';
  mount();
  await screen.findByText("Choose an existing model to continue.");
  expect(screen.queryByText("Add a model first.")).toBeNull();
  expect(
    screen
      .getByRole("button", { name: "Turn on Bot" })
      .hasAttribute("disabled"),
  ).toBe(true);
});

it("uploads ordered attachments and renders admitted input before a slow snapshot refresh", async () => {
  const view = mount();
  await screen.findByRole("button", { name: "Attach files" });
  typeMessage("Inspect ");
  fireEvent.change(view.container.querySelector("input[type=file]")!, {
    target: {
      files: [new File(["hello"], "notes.txt", { type: "text/plain" })],
    },
  });
  await screen.findByRole("button", { name: "notes.txt" });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Send" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  let release!: () => void;
  botReadGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(requests[0].parts).toEqual([
    { attachment_id: attachment.attachment_id },
    "Inspect ",
  ]);
  await waitFor(() => expect(draft.doc.getText("text").toString()).toBe(""));
  expect(screen.getByText("Inspect")).toBeTruthy();
  expect(screen.getByText("notes.txt")).toBeTruthy();
  await act(async () => {
    release();
  });
});

it("retains failed uploads, retries them, and submits attachment-only exact commands", async () => {
  uploadFailure = true;
  const view = mount();
  await screen.findByRole("button", { name: "Attach files" });
  fireEvent.change(view.container.querySelector("input[type=file]")!, {
    target: {
      files: [new File(["hello"], "notes.txt", { type: "text/plain" })],
    },
  });
  const failed = await screen.findByRole("button", {
    name: /^notes.txt - upload failed/,
  });
  expect(
    screen.getByRole("button", { name: "Send" }).hasAttribute("disabled"),
  ).toBe(true);
  uploadFailure = false;
  fireEvent.click(failed);
  await screen.findByRole("button", { name: "notes.txt" });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Send" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  reject = true;
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByRole("button", { name: "Retry exactly" });
  expect(requests[0].text).toBe("");
  expect(requests[0].parts).toEqual([
    { attachment_id: attachment.attachment_id },
  ]);
  expect(
    screen
      .getByRole("button", { name: "Attach files" })
      .hasAttribute("disabled"),
  ).toBe(true);
  fireEvent.click(screen.getByText("Leave"));
  fireEvent.click(screen.getByText("Return"));
  reject = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry exactly" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toEqual(requests[0]);
});

it("removes transcript activity when work settles or requires a decision", async () => {
  snapshot.inputs = [
    {
      input_id: "active",
      source: "user",
      text: "Watch",
      state: "admitted",
      created_at: "2026-11-12T00:00:00Z",
    },
  ];
  mount();
  await screen.findByText("Preparing…");
  await act(async () => {
    queries.setQueryData(["bot"], {
      ...snapshot,
      pending_decision: true,
      operation: {
        status: "running",
        receipt: { receipt_id: "receipt-active", thread_id: BOT_THREAD_ID },
      },
    });
  });
  await waitFor(() => expect(screen.queryByText("Preparing…")).toBeNull());
  await act(async () => {
    queries.setQueryData(["bot"], { ...snapshot, inputs: [] });
  });
  await waitFor(() => expect(screen.queryByText("Preparing…")).toBeNull());
});

it("echoes the input before admission and reconciles it without clearing a newer draft", async () => {
  let release!: () => void;
  sendGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  typeMessage("Immediate echo");
  const send = screen.getByRole("button", { name: "Send" });
  fireEvent.click(send);
  fireEvent.click(send);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(draft.doc.getText("text").toString()).toBe("Immediate echo");
  expect(screen.getAllByText("Immediate echo")).toHaveLength(2);
  expect(screen.getAllByText("Sending…").length).toBeGreaterThan(0);
  typeMessage("Next draft");
  await act(async () => {
    release();
  });
  await waitFor(() => expect(screen.queryByText("Sending…")).toBeNull());
  expect(screen.getAllByText("Immediate echo")).toHaveLength(1);
  expect(draft.doc.getText("text").toString()).toBe("Next draft");
});

it("resolves an unknown submission from a snapshot without replaying it", async () => {
  reject = true;
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  typeMessage("Observe only");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Not confirmed");
  snapshot.messages = [
    {
      message_id: `user-${requests[0].input_id}`,
      role: "user",
      text: "Observe only",
      created_at: "2026-11-12T00:00:00Z",
    },
  ];
  fireEvent.click(screen.getByRole("button", { name: "Check status" }));
  await waitFor(() => expect(screen.queryByText("Not confirmed")).toBeNull());
  expect(screen.queryByRole("button", { name: "Retry exactly" })).toBeNull();
  expect(screen.getAllByText("Observe only")).toHaveLength(2);
  expect(draft.doc.getText("text").toString()).toBe("Observe only");
  expect(requests).toHaveLength(1);
  expect(
    screen.getByRole("textbox", { name: "Shared prompt" }).closest("[inert]"),
  ).toBeNull();
});

it("keeps a rejected row until explicit edit or discard, then uses a new identity", async () => {
  rejectionStatus = 409;
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  typeMessage("Correct this");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Not sent");
  expect(draft.doc.getText("text").toString()).toBe("Correct this");
  fireEvent.click(screen.getByRole("button", { name: "Edit message" }));
  expect(draft.doc.getText("text").toString()).toBe("Correct this");
  rejectionStatus = undefined;
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1].input_id).not.toBe(requests[0].input_id);
});

it("blocks preparation without sending or losing the draft and offers Stop for active work", async () => {
  snapshot.operation = {
    receipt: {
      receipt_id: "receipt-preparing",
      thread_id: BOT_THREAD_ID,
      submitted_at: "2026-11-12T00:00:00Z",
    },
    status: "preparing",
  };
  mount();
  await screen.findByText(
    "Bot is starting. Your draft is kept; send when it is working.",
  );
  expect(
    screen.getByRole("button", { name: "Stop" }).hasAttribute("disabled"),
  ).toBe(false);
  typeMessage("Keep this draft");
  expect(
    screen.getByRole("button", { name: "Steer" }).hasAttribute("disabled"),
  ).toBe(true);
  expect(requests).toHaveLength(0);
  expect(draft.doc.getText("text").toString()).toBe("Keep this draft");
  snapshot = {
    ...snapshot,
    operation: { ...snapshot.operation, status: "running" },
  };
  await act(async () => {
    queries.setQueryData(["bot"], snapshot);
  });
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Steer" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  fireEvent.click(screen.getByRole("button", { name: "Steer" }));
  await waitFor(() => expect(requests).toHaveLength(1));
});

it("retains unknown input and exact recovery when interruption precedes user publication", async () => {
  reject = true;
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  typeMessage("Keep this interrupted input");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Not confirmed");
  snapshot = {
    ...snapshot,
    inputs: [
      {
        input_id: requests[0].input_id,
        source: "user",
        text: requests[0].text ?? "",
        state: "interrupted",
        created_at: "2026-11-12T00:00:00Z",
      },
    ],
  };
  await act(async () => {
    await queries.invalidateQueries({ queryKey: ["bot"] });
  });
  expect(screen.getAllByText("Keep this interrupted input")).toHaveLength(2);
  expect(screen.getByText("Not confirmed")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Retry exactly" })).toBeTruthy();
  expect(requests).toHaveLength(1);
  reject = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry exactly" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toEqual(requests[0]);
});

it("keeps Bot context and cost visible while Debug reveals only left inspection controls", async () => {
  snapshot.thread = { thread_id: BOT_THREAD_ID } as Schema<"ThreadSummary">;
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  typeMessage("Keep this draft");
  expect(await screen.findByLabelText("Work inspection")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Inspect tasks" })).toBeNull();
  expect(
    await screen.findByRole("button", { name: "Voice input" }),
  ).toBeTruthy();
  expect(screen.getByRole("button", { name: "Attach files" })).toBeTruthy();
  fireEvent.keyDown(window, { ctrlKey: true, altKey: true, code: "KeyD" });
  expect(
    await screen.findByRole("button", { name: "Inspect tasks" }),
  ).toBeTruthy();
  fireEvent.keyDown(window, { ctrlKey: true, altKey: true, code: "KeyD" });
  expect(await screen.findByLabelText("Work inspection")).toBeTruthy();
  expect(screen.queryByRole("button", { name: "Inspect tasks" })).toBeNull();
  expect(draft.doc.getText("text").toString()).toBe("Keep this draft");
});

it.each([false, true])(
  "keeps ordinary Thread inspection visible with Debug=%s",
  async (debug) => {
    localStorage.setItem("a13n-harness-ui.debug", debug ? "on" : "off");
    mount("/threads/thread-ordinary");
    expect(await screen.findByLabelText("Work inspection")).toBeTruthy();
  },
);

it.each([
  [false, false],
  [false, true],
  [true, false],
  [true, true],
])(
  "dictates into Bot draft once without sending (busy=%s, debug=%s)",
  async (busy, debug) => {
    snapshot.thread = { thread_id: BOT_THREAD_ID } as Schema<"ThreadSummary">;
    localStorage.setItem("a13n-harness-ui.debug", debug ? "on" : "off");
    if (busy)
      snapshot.operation = {
        receipt: {
          receipt_id: "busy",
          thread_id: BOT_THREAD_ID,
          submitted_at: "2026-11-12T00:00:00Z",
        },
        status: "running",
      };
    let finish!: (text: string) => void;
    voiceResponse = new Promise((resolve) => {
      finish = resolve;
    });
    vi.mocked(recordAudio).mockResolvedValue(
      vi.fn(async () => new Blob(["sample"])),
    );
    mount();
    fireEvent.click(await screen.findByRole("button", { name: "Voice input" }));
    fireEvent.click(
      await screen.findByRole("button", { name: "Stop recording" }),
    );
    await waitFor(() => expect(voiceRequests).toHaveLength(1));
    typeMessage("Typed while waiting. ");
    finish("Spoken draft");
    await waitFor(() =>
      expect(draft.doc.getText("text").toString()).toContain("Spoken draft"),
    );
    expect(draft.doc.getText("text").toString()).toContain(
      "Typed while waiting.",
    );
    expect(
      draft.doc
        .getText("text")
        .toString()
        .match(/Spoken draft/g),
    ).toHaveLength(1);
    expect(requests).toHaveLength(0);
    expect(recordAudio).toHaveBeenCalledTimes(1);
    expect(voiceRequests).toHaveLength(1);
    expect(voiceRequests[0]).toMatchObject({ thread_id: BOT_THREAD_ID });
    expect(screen.getByLabelText("Work inspection")).toBeTruthy();
    expect(!!screen.queryByRole("button", { name: "Inspect tasks" })).toBe(
      debug,
    );
  },
);

it("hides only the known Host interruption placeholder outside Debug", async () => {
  const placeholder =
    "Bot processing was interrupted. Review the reported error and working Thread results before continuing.";
  snapshot.state.last_error = placeholder;
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  expect(screen.queryByText(placeholder)).toBeNull();
  fireEvent.keyDown(window, { ctrlKey: true, altKey: true, code: "KeyD" });
  expect(await screen.findByText(placeholder)).toBeTruthy();
});

it("preserves actionable Bot failures outside Debug", async () => {
  snapshot.state.last_error =
    "Provider rejected the request: account unavailable";
  mount();
  expect(await screen.findByText(snapshot.state.last_error)).toBeTruthy();
});

it("enables Bot with an independent Agent and default Model through one source publication", async () => {
  snapshot.enabled = false;
  mount();
  const enable = await screen.findByRole("button", { name: "Turn on Bot" });
  await waitFor(() => expect(enable.hasAttribute("disabled")).toBe(false));
  const user = userEvent.setup();
  await user.click(screen.getByRole("combobox", { name: "Bot agent" }));
  await user.click(await screen.findByRole("option", { name: "Coordinator" }));
  expect(
    screen.getByRole("combobox", { name: "Bot model" }).textContent,
  ).toContain("Default (Alternate)");
  await user.click(enable);
  await screen.findByRole("button", { name: "Send" });
  const document = readDocument(configurationWrites[0])!.toJS();
  expect(document.webui.bot.agent).toBe("agent-coordinator");
  expect(document.webui.bot.model).toBeUndefined();
  expect(configurationWrites).toHaveLength(1);
  expect(requests).toHaveLength(0);
});

it("requires a usable selected Agent Model or explicit override and preserves that override on Agent changes", async () => {
  snapshot.enabled = false;
  mount();
  const enable = await screen.findByRole("button", { name: "Turn on Bot" });
  await waitFor(() => expect(enable.hasAttribute("disabled")).toBe(false));
  const user = userEvent.setup();
  const agent = screen.getByRole("combobox", { name: "Bot agent" });
  const model = screen.getByRole("combobox", { name: "Bot model" });
  await user.click(agent);
  await user.click(await screen.findByRole("option", { name: "No model" }));
  expect(enable.hasAttribute("disabled")).toBe(true);
  expect(model.textContent).toContain("Default (no model)");
  await user.click(model);
  await user.click(await screen.findByRole("option", { name: "Primary" }));
  expect(enable.hasAttribute("disabled")).toBe(false);
  await user.click(agent);
  await user.click(await screen.findByRole("option", { name: "Coordinator" }));
  expect(model.textContent).toContain("Primary");
  await user.click(enable);
  await screen.findByRole("button", { name: "Send" });
  const document = readDocument(configurationWrites[0])!.toJS();
  expect(document.webui.bot).toMatchObject({
    agent: "agent-coordinator",
    model: "model-primary",
  });
});

it("does not enable an unavailable explicit Bot Agent even with a valid explicit Model", async () => {
  snapshot.enabled = false;
  rootSource = rootSource.replace(
    "bot: {enabled: false}",
    "bot: {enabled: false, agent: agent-missing, model: model-primary}",
  );
  mount();
  expect(
    (await screen.findByRole("combobox", { name: "Bot agent" })).textContent,
  ).toContain("agent-missing (unavailable)");
  expect(
    screen
      .getByRole("button", { name: "Turn on Bot" })
      .hasAttribute("disabled"),
  ).toBe(true);
  expect(configurationWrites).toHaveLength(0);
});

function unloadPrevented() {
  const event = new Event("beforeunload", { cancelable: true });
  window.dispatchEvent(event);
  return event.defaultPrevented;
}

it("protects unacknowledged Bot edits but allows reload after synchronization", async () => {
  mount();
  const editor = await screen.findByRole("textbox", { name: "Shared prompt" });
  expect(editor.getAttribute("aria-description")).toContain("Shared draft.");
  syncDraft = false;
  typeMessage("Not synchronized yet");
  expect(unloadPrevented()).toBe(true);
  await act(async () => acknowledgeDraft());
  expect(unloadPrevented()).toBe(false);
  // Acknowledged content is retained even when the connection drops.
  act(() => {
    draft.status = "Disconnected";
    draft.notify();
  });
  expect(unloadPrevented()).toBe(false);
});

it("protects pending and unknown delivery, then releases the reload guard on reconciliation", async () => {
  let release!: () => void;
  sendGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  reject = true;
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  typeMessage("Keep exact command");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(unloadPrevented()).toBe(true);
  await act(async () => release());
  await screen.findByText("Not confirmed");
  expect(unloadPrevented()).toBe(true);
  snapshot.messages = [
    {
      message_id: `user-${requests[0].input_id}`,
      role: "user",
      text: "Keep exact command",
      created_at: "2026-11-12T00:00:00Z",
    },
  ];
  fireEvent.click(screen.getByRole("button", { name: "Check status" }));
  await waitFor(() => expect(screen.queryByText("Not confirmed")).toBeNull());
  expect(unloadPrevented()).toBe(false);
});

it("allows reload of a synchronized rejected draft without losing its editable content", async () => {
  rejectionStatus = 409;
  mount();
  await screen.findByRole("textbox", { name: "Shared prompt" });
  typeMessage("Retained after rejection");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Not sent");
  expect(unloadPrevented()).toBe(false);
  expect(draft.doc.getText("text").toString()).toBe("Retained after rejection");
});

function publishFallback() {
  snapshot.thread = { thread_id: BOT_THREAD_ID } as Schema<"ThreadSummary">;
  snapshot.messages = [
    {
      message_id: "proactive-fallback",
      role: "assistant",
      text: "Readable publication",
      created_at: "2026-11-12T00:00:00Z",
    },
  ];
}

it("shows initial Bot history failure and retries without hiding publications", async () => {
  publishFallback();
  historyFailure = true;
  mount();
  await screen.findByText("History unavailable");
  expect(screen.getByText("Readable publication")).toBeTruthy();
  historyFailure = false;
  historyPage = {
    entries: [
      {
        position: 0,
        message_kind: "response",
        timestamp: "2026-11-12T00:00:00Z",
        parts: [{ kind: "assistant", text: "Recovered native work" }],
      },
    ],
    turns: [],
    total: 1,
  };
  fireEvent.click(screen.getByRole("button", { name: "Retry" }));
  await screen.findByText("Recovered native work");
  expect(screen.queryByText("History unavailable")).toBeNull();
  expect(screen.getByText("Readable publication")).toBeTruthy();
});

it("shows Bot history loading alongside readable publications", async () => {
  publishFallback();
  let release!: () => void;
  historyGate = new Promise<void>((resolve) => {
    release = resolve;
  });
  mount();
  await screen.findByText("Loading messages…");
  expect(screen.getByText("Readable publication")).toBeTruthy();
  await act(async () => release());
  await waitFor(() =>
    expect(screen.queryByText("Loading messages…")).toBeNull(),
  );
});

it("keeps Bot next-page failure on its pagination retry surface", async () => {
  publishFallback();
  earlierHistoryFailure = true;
  historyPage = {
    entries: [
      {
        position: 1,
        message_kind: "response",
        timestamp: "2026-11-12T00:00:00Z",
        parts: [{ kind: "assistant", text: "Loaded native work" }],
      },
    ],
    turns: [],
    total: 2,
    next_cursor: "older",
  };
  mount();
  await screen.findByText("Readable publication");
  await waitFor(() =>
    expect(
      queries.getQueryState([
        "thread",
        BOT_THREAD_ID,
        "history",
        "bot-continuation",
      ])?.status,
    ).toBe("success"),
  );
  const reader = document.querySelector(`.${conversationStyles.reading}`)!;
  // Use the real reader's scroll interaction to request the earlier page.
  fireEvent.scroll(reader);
  await screen.findByRole("button", { name: "Retry loading" });
  expect(screen.queryByText("History unavailable")).toBeNull();
  expect(screen.getByText("Readable publication")).toBeTruthy();
  earlierHistoryFailure = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry loading" }));
  await waitFor(() =>
    expect(screen.queryByRole("button", { name: "Retry loading" })).toBeNull(),
  );
});

it("retains readable Bot publications when native detail is temporarily missing", async () => {
  publishFallback();
  detailMissing = true;
  mount();
  await screen.findByText("Readable publication");
  await screen.findByText("Chat not found");
  expect(screen.queryByText("Loading messages…")).toBeNull();
  expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBeTruthy();
});

it("consolidates narrow Bot header actions into one menu in desktop order", async () => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query === "(max-width: 760px)",
    addEventListener() {},
    removeEventListener() {},
  }));
  snapshot = { ...snapshot, enabled: true, available: true };
  queries.setQueryData(["bot"], snapshot);
  const supporting = vi.fn();
  render(
    <TransportContext value={transport}>
      <QueryClientProvider client={queries}>
        <MemoryRouter>
          <BotActions
            trailingActions={
              <MenuItem onClick={supporting}>Open supporting panel</MenuItem>
            }
          >
            <MenuItem>Work notes</MenuItem>
          </BotActions>
        </MemoryRouter>
      </QueryClientProvider>
    </TransportContext>,
  );
  expect(screen.getAllByRole("button")).toHaveLength(1);
  await userEvent.click(screen.getByRole("button", { name: "Chat actions" }));
  expect(
    (await screen.findAllByRole("menuitem")).map((item) => item.textContent),
  ).toEqual([
    "Work notes",
    "Bot settings",
    "Pause automation",
    "Open supporting panel",
  ]);
  await userEvent.click(
    screen.getByRole("menuitem", { name: "Open supporting panel" }),
  );
  expect(supporting).toHaveBeenCalledOnce();
});
