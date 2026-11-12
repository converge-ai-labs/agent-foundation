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
import { Link, MemoryRouter, Route, Routes, useLocation } from "react-router";
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { BotActions, BotComposerProvider, BotPage, BOT_THREAD_ID } from "./bot";
import { ConversationPage } from "./conversations/conversation";
import type { DecisionResponse } from "./conversations/decision-inputs";
import { createTransport, type Schema } from "./transport/client";
import { TransportContext } from "./transport/context";
import { ComposerDrafts } from "./conversations/composer";
import { ThreadDraft } from "./conversations/draft";
import { DraftContext, type SourceDraft } from "./configuration/drafts";
import { readDocument } from "./configuration/documents";

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
let requests: Schema<"BotSubmit">[];
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
let rootSource: string;
let configurationWrites: string[];
let writable: boolean;
let saveFailure: "validation" | "unknown" | null;
let saveGate: Promise<void> | undefined;
let savedDrafts: Map<string, SourceDraft>;
let queries: QueryClient;
let transport: ReturnType<typeof createTransport>;
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
  draft = new ThreadDraft();
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
  vi.spyOn(document, "hasFocus").mockReturnValue(true);
  vi.stubGlobal(
    "fetch",
    vi.fn(async (request: Request) => {
      const path = new URL(request.url).pathname;
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
      if (path === "/api/bot" && botReadGate) await botReadGate;
      if (path === "/api/bot/pause") {
        snapshot = {
          ...snapshot,
          state: { ...snapshot.state, ...(await request.json()) },
        };
      }
      if (path === `/api/threads/${BOT_THREAD_ID}`)
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
          agents: [
            {
              agent_id: "agent-assistant",
              model_id: "model-primary",
              name: "Assistant",
            },
          ],
          models: [
            {
              model_id: "model-primary",
              name: "Primary",
              route: "openai:gpt-5",
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
    <TransportContext value={transport}>
      <QueryClientProvider client={queries}>
        <DraftContext value={savedDrafts}>
          <ComposerDrafts value={new Map([[BOT_THREAD_ID, draft]])}>
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
    </TransportContext>,
  );
}
it("submits to the lazy root without browser registration and clears accepted input", async () => {
  mount();
  await screen.findByRole("textbox", { name: "Message" });
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
  await screen.findByRole("textbox", { name: "Message" });
  typeMessage("Do once");
  await waitFor(() =>
    expect(
      screen.getByRole("button", { name: "Send" }).hasAttribute("disabled"),
    ).toBe(false),
  );
  fireEvent.click(screen.getByText("Leave"));
  fireEvent.click(screen.getByText("Return"));
  expect(screen.getByRole("textbox", { name: "Message" }).textContent).toBe(
    "Do once",
  );
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByRole("button", { name: "Retry exactly" });
  expect(requests).toHaveLength(1);
  fireEvent.click(screen.getByText("Leave"));
  fireEvent.click(screen.getByText("Return"));
  expect(
    screen.getByRole("textbox", { name: "Message" }).closest("[inert]"),
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
  expect(screen.queryByRole("textbox", { name: "Message" })).toBeNull();
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
    screen.getByRole("button", { name: "Saving…" }).hasAttribute("disabled"),
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

it("redirects the reserved Thread URL to Bot chat without exposing execution history or sharing", async () => {
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
  ).toBe(false);
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
  await screen.findByRole("textbox", { name: "Message" });
  typeMessage("Immediate echo");
  const send = screen.getByRole("button", { name: "Send" });
  fireEvent.click(send);
  fireEvent.click(send);
  await waitFor(() => expect(requests).toHaveLength(1));
  expect(draft.doc.getText("text").toString()).toBe("");
  expect(screen.getByText("Immediate echo")).toBeTruthy();
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
  await screen.findByRole("textbox", { name: "Message" });
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
  expect(screen.getAllByText("Observe only")).toHaveLength(1);
  expect(requests).toHaveLength(1);
  expect(
    screen.getByRole("textbox", { name: "Message" }).closest("[inert]"),
  ).toBeNull();
});

it("keeps a rejected row until explicit edit or discard, then uses a new identity", async () => {
  rejectionStatus = 409;
  mount();
  await screen.findByRole("textbox", { name: "Message" });
  typeMessage("Correct this");
  fireEvent.click(screen.getByRole("button", { name: "Send" }));
  await screen.findByText("Not sent");
  expect(draft.doc.getText("text").toString()).toBe("");
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
  await screen.findByRole("textbox", { name: "Message" });
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
  expect(screen.getByText("Keep this interrupted input")).toBeTruthy();
  expect(screen.getByText("Not confirmed")).toBeTruthy();
  expect(screen.getByRole("button", { name: "Retry exactly" })).toBeTruthy();
  expect(requests).toHaveLength(1);
  reject = false;
  fireEvent.click(screen.getByRole("button", { name: "Retry exactly" }));
  await waitFor(() => expect(requests).toHaveLength(2));
  expect(requests[1]).toEqual(requests[0]);
});
