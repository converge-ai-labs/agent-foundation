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
import userEvent from "@testing-library/user-event";
import { QueryClient, QueryClientProvider } from "@tanstack/react-query";
import { EditorView } from "@codemirror/view";
import * as Y from "yjs";
import { Composer, ComposerDrafts } from "./composer";
import { ThreadDraft, encode, values } from "./draft";
import { LanguageChoice, LanguageProvider } from "../i18n/language";
import { i18n } from "../i18n";
import { ApiError, type Schema, type Transport } from "../transport/client";
import { TransportContext } from "../transport/context";

const queries: QueryClient[] = [];
beforeEach(() => {
  vi.spyOn(navigator, "languages", "get").mockReturnValue(["en-US"]);
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
  Object.defineProperty(Element.prototype, "getAnimations", {
    configurable: true,
    value: () => [],
  });
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
});
afterEach(async () => {
  cleanup();
  queries.splice(0).forEach((query) => query.clear());
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  localStorage.clear();
  await i18n.changeLanguage("en");
});

async function language(name: string) {
  await userEvent.click(screen.getByRole("combobox"));
  await userEvent.click(await screen.findByRole("option", { name }));
}

import { AppearanceProvider } from "../appearance/react";
import { AppearanceStore } from "../appearance/store";

function mount({
  draft = new ThreadDraft(),
  local = false,
  activity = { state: "inactive" } as Schema<"RootActivityView">,
  post = vi.fn(),
  get = vi.fn(),
}: {
  draft?: ThreadDraft;
  local?: boolean;
  activity?: Schema<"RootActivityView">;
  post?: ReturnType<typeof vi.fn>;
  get?: ReturnType<typeof vi.fn>;
} = {}) {
  const close = vi.fn();
  const connect = vi
    .spyOn(draft, "connect")
    .mockReturnValue({ presence: vi.fn(), close });
  const query = new QueryClient({
    defaultOptions: { queries: { retry: false }, mutations: { retry: false } },
  });
  queries.push(query);
  const transport = {
    client: { POST: post, GET: get },
    fetch: vi.fn(),
  } as unknown as Transport;
  const appearance = new AppearanceStore(window);
  const view = render(
    <AppearanceProvider store={appearance}>
      <QueryClientProvider client={query}>
        <TransportContext value={transport}>
          <LanguageProvider>
            <LanguageChoice />
            <ComposerDrafts value={new Map([["chat-one", draft]])}>
              <Composer
                threadId="chat-one"
                activity={activity}
                local={local}
                canRun
                profile={{ display_name: "Alice", color: "#2563eb" }}
                unauthorized={() => {}}
                reconcile={() => {}}
              />
            </ComposerDrafts>
          </LanguageProvider>
        </TransportContext>
      </QueryClientProvider>
    </AppearanceProvider>,
  );
  return { ...view, draft, post, get, transport, connect, close, appearance };
}

function synchronizedDraft(text = "") {
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, text);
  draft.receive({
    draft_id: "draft-one",
    participant_id: "alice",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
  });
  return draft;
}

it("keeps the current language when rejoining a replacement shared draft", async () => {
  const draft = synchronizedDraft();
  const { post, connect, close } = mount({ draft });
  const original = screen.getByRole("textbox", { name: "Shared prompt" });
  await language("简体中文");
  const description = original.getAttribute("aria-description");
  expect(original.textContent).toContain("提出问题，或描述任务…");
  const replacement = new Y.Doc();
  act(() =>
    draft.receive({
      draft_id: "draft-two",
      participant_id: "alice",
      participants: {},
      update_base64: encode(Y.encodeStateAsUpdate(replacement)),
    }),
  );
  replacement.destroy();
  await userEvent.click(screen.getByRole("button", { name: "保留我的输入" }));
  const rejoined = screen.getByRole("textbox", { name: "共享输入" });
  expect(rejoined).not.toBe(original);
  expect(rejoined.textContent).toContain("提出问题，或描述任务…");
  expect(rejoined.getAttribute("aria-description")).toBe(description);
  expect(draft.draftId).toBe("draft-two");
  expect(values(draft.doc).prompt).toBe("");
  const editor = EditorView.findFromDOM(rejoined)!;
  act(() =>
    editor.dispatch({
      changes: { from: 0, insert: "保留 source_id" },
      userEvent: "input.type",
    }),
  );
  await language("English");
  expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBe(rejoined);
  expect(values(draft.doc).prompt).toBe("保留 source_id");
  act(() => draft.undo.undo());
  expect(rejoined.textContent).toContain("Ask anything, or describe a task…");
  expect(connect).toHaveBeenCalledOnce();
  expect(close).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
});

it("switches the real language control without replacing the editor, selection, undo history or staged image", async () => {
  const createObjectURL = vi.fn().mockReturnValue("blob:local-image");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    },
  );
  const { draft, container, post, transport, appearance } = mount({
    local: true,
  });
  const textbox = screen.getByRole("textbox", { name: "Message" });
  const editor = EditorView.findFromDOM(textbox)!;
  const document = draft.doc;
  const undo = draft.undo;
  expect(textbox.textContent).toContain("Ask anything, or describe a task…");
  await language("简体中文");
  expect(textbox.textContent).toContain("提出问题，或描述任务…");
  expect(window.document.documentElement.lang).toBe("zh-CN");
  expect(draft.doc.getText("text").toString()).toBe("");
  act(() =>
    editor.dispatch({
      changes: { from: 0, insert: "Keep source_id and 用户输入" },
      userEvent: "input.type",
    }),
  );
  fireEvent.change(container.querySelector('input[type="file"]')!, {
    target: {
      files: [new File(["image"], "中文-source.png", { type: "image/png" })],
    },
  });
  await screen.findByRole("button", {
    name: "中文-source.png - 待上传",
  });
  const originalText = draft.doc.getText("text").toString();
  act(() => {
    draft.undo.stopCapturing();
    editor.dispatch({
      changes: { from: 0, insert: "edit " },
      selection: { anchor: 5, head: 9 },
      userEvent: "input.type",
    });
    draft.undo.stopCapturing();
  });
  const chip = screen.getByRole("button", { name: "中文-source.png - 待上传" });
  const image = chip.querySelector("img");
  const imageCreations = createObjectURL.mock.calls.length;
  const imageReleases = revokeObjectURL.mock.calls.length;
  const selection = editor.state.selection.toJSON();
  await language("English");
  act(() => {
    appearance.setSize("composer", 20);
    appearance.setTheme("dark");
  });
  expect(screen.getByRole("textbox", { name: "Message" })).toBe(textbox);
  expect(EditorView.findFromDOM(textbox)).toBe(editor);
  expect(editor.state.selection.toJSON()).toEqual(selection);
  expect(draft.doc).toBe(document);
  expect(draft.undo).toBe(undo);
  expect(
    screen.getByRole("button", { name: "中文-source.png - ready to upload" }),
  ).toBe(chip);
  expect(chip.querySelector("img")).toBe(image);
  expect(createObjectURL).toHaveBeenCalledTimes(imageCreations);
  expect(revokeObjectURL).toHaveBeenCalledTimes(imageReleases);
  expect(transport.fetch).not.toHaveBeenCalled();
  expect(post).not.toHaveBeenCalled();
  act(() => undo.undo());
  expect(draft.doc.getText("text").toString()).toBe(originalText);
  expect(screen.getByRole("button", { name: "Send" })).toBeTruthy();
  await language("System");
  expect(screen.getByRole("textbox", { name: "Message" })).toBe(textbox);
});

it.each(["accepted", "unknown"] as const)(
  "keeps one captured submission while switching language before an %s response",
  async (outcome) => {
    const draft = synchronizedDraft("Keep English and 中文 exactly.");
    let resolve!: (value: unknown) => void;
    let reject!: (reason: unknown) => void;
    const post = vi.fn().mockReturnValue(
      new Promise((yes, no) => {
        resolve = yes;
        reject = no;
      }),
    );
    const { connect, close } = mount({ draft, post });
    const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
    fireEvent.click(screen.getByRole("button", { name: "Send" }));
    await waitFor(() => expect(post).toHaveBeenCalledOnce());
    const request = post.mock.calls[0][1];
    const sourceId = request.body.source_id;
    await language("简体中文");
    expect(screen.getByRole("button", { name: "正在提交" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(screen.getByRole("textbox", { name: "共享输入" })).toBe(textbox);
    expect(post).toHaveBeenCalledOnce();
    expect(request.body.parts).toEqual(["Keep English and 中文 exactly."]);
    await act(async () => {
      if (outcome === "accepted")
        resolve({ data: { receipt_id: "receipt-one", thread_id: "chat-one" } });
      else reject(new Error("proxy disconnected"));
    });
    if (outcome === "accepted") {
      await waitFor(() => expect(draft.submission.kind).toBe("accepted"));
      expect(values(draft.doc).prompt).toBe("");
    } else {
      expect(await screen.findByRole("alert")).toHaveProperty(
        "textContent",
        expect.stringContaining("无法确认送达"),
      );
      expect(values(draft.doc).prompt).toBe("Keep English and 中文 exactly.");
      expect(screen.getByRole("button", { name: "发送" })).toHaveProperty(
        "disabled",
        true,
      );
    }
    await language("English");
    expect(post).toHaveBeenCalledOnce();
    expect(draft.localInputs[0].id).toBe(sourceId);
    expect(connect).toHaveBeenCalledOnce();
    expect(close).not.toHaveBeenCalled();
    if (outcome === "unknown")
      expect(screen.getByRole("alert").textContent).toContain(
        "Couldn't confirm delivery",
      );
  },
);

it.each(["waiting", "rejected", "uncertain"] as const)(
  "translates a %s Stop without replaying it or translating its diagnostic",
  async (phase) => {
    let settle!: (value: unknown) => void;
    let reject!: (reason: unknown) => void;
    const post = vi.fn().mockReturnValue(
      new Promise((yes, no) => {
        settle = yes;
        reject = no;
      }),
    );
    const get = vi.fn().mockReturnValue(new Promise(() => {}));
    const draft = synchronizedDraft();
    const { connect, close } = mount({
      draft,
      post,
      get,
      activity: {
        state: "running",
        receipt_id: "current",
        available_actions: ["steer", "cancel"],
      },
    });
    fireEvent.click(screen.getByRole("button", { name: "Stop" }));
    await screen.findByText("Requesting Stop…");
    const request = draft.stop;
    await language("简体中文");
    await screen.findByText("正在请求停止…");
    expect(post).toHaveBeenCalledOnce();
    await act(async () => {
      if (phase === "waiting")
        settle({ data: { receipt_id: "current", accepted: true } });
      else
        reject(
          phase === "rejected"
            ? new ApiError("vendor_id=42: quota exceeded 原文", 409)
            : new Error("proxy lost response"),
        );
    });
    await waitFor(() => expect(draft.stop?.phase).toBe(phase));
    if (phase === "waiting")
      await screen.findByText("正在停止…等待执行和清理结束。");
    if (phase === "rejected")
      expect(screen.getByRole("alert").textContent).toContain(
        "停止请求未被接受。vendor_id=42: quota exceeded 原文",
      );
    if (phase === "uncertain")
      expect(screen.getByRole("alert").textContent).toContain("请求未重发");
    const observations = get.mock.calls.length;
    await language("English");
    expect(draft.stop).toBe(request);
    expect(post).toHaveBeenCalledOnce();
    expect(get).toHaveBeenCalledTimes(observations);
    expect(connect).toHaveBeenCalledOnce();
    expect(close).not.toHaveBeenCalled();
    if (phase === "rejected")
      expect(screen.getByRole("alert").textContent).toContain(
        "Stop was not accepted. vendor_id=42: quota exceeded 原文",
      );
    act(() => draft.doc.getText("text").insert(0, "Next instruction"));
    expect(screen.getByRole("button", { name: "Steer" })).toHaveProperty(
      "disabled",
      true,
    );
    expect(post).toHaveBeenCalledOnce();
  },
);

it("keeps previewed file bytes verbatim even when they equal a translated application notice", async () => {
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = vi.fn().mockReturnValue("blob:preview");
      static revokeObjectURL = vi.fn();
    },
  );
  const source = "Binary attachment. Download to inspect its original bytes.";
  const draft = new ThreadDraft();
  draft.addAttachment("attachment-one");
  draft.receive({
    draft_id: "draft-one",
    participant_id: "alice",
    participants: {},
    update_base64: encode(Y.encodeStateAsUpdate(draft.doc)),
  });
  const get = vi.fn().mockResolvedValue({
    data: {
      attachment_id: "attachment-one",
      name: "source.txt",
      size: source.length,
      media_type: "text/plain",
    },
  });
  const { transport, post } = mount({ draft, get });
  vi.mocked(transport.fetch).mockResolvedValue({
    blob: async () => ({
      size: source.length,
      arrayBuffer: async () => new TextEncoder().encode(source).buffer,
    }),
  } as Response);
  await userEvent.click(
    await screen.findByRole("button", { name: "source.txt" }),
  );
  await screen.findByRole("dialog");
  // The modal traps focus; switch the same runtime while it remains open.
  await act(() => i18n.changeLanguage("zh-CN"));
  expect(screen.getByRole("dialog").querySelector("pre")?.textContent).toBe(
    source,
  );
  expect(
    screen.getByRole("link", { name: "下载原文件" }).getAttribute("download"),
  ).toBe("source.txt");
  expect(transport.fetch).toHaveBeenCalledOnce();
  expect(post).not.toHaveBeenCalled();
});
