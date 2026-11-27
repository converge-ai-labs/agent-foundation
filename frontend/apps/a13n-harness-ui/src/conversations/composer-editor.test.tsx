// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
  act,
} from "@testing-library/react";
import * as Y from "yjs";
import { SendShortcutContext } from "./send-shortcut";
import { ComposerEditor } from "./composer-editor";
import { ThreadDraft, encode, values } from "./draft";
import type { EditorView } from "@codemirror/view";
import type { ComposerAttachmentView } from "./composer-attachments";
import type { Transport } from "../transport/client";
import { attachmentSelections, attachmentToken } from "./inline-attachments";

beforeEach(() => {
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
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
});
it("mounts the actual CodeMirror binding and retains remote updates without replacing the editor", async () => {
  const draft = new ThreadDraft();
  const submit = vi.fn();
  render(
    <ComposerEditor
      draft={draft}
      profile={{ display_name: "Alice", color: "#2563eb" }}
      presence={() => {}}
      submit={submit}
    />,
  );
  const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
  act(() => {
    draft.doc.getText("text").insert(0, "Hello world");
  });
  await waitFor(() => expect(textbox.textContent).toContain("Hello world"));
  expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBe(textbox);
  fireEvent.keyDown(textbox, { key: "Enter" });
  expect(submit).toHaveBeenCalledTimes(1);
  expect(draft.doc.getText("text").toString()).toBe("Hello world");
  fireEvent.keyDown(textbox, { key: "Enter", shiftKey: true });
  expect(submit).toHaveBeenCalledTimes(1);
  expect(draft.doc.getText("text").toString()).toContain("\n");
  fireEvent.keyDown(textbox, { key: "Enter", ctrlKey: true });
  expect(submit).toHaveBeenCalledTimes(2);
});
it("updates the mounted editor when the accepted shortcut changes", () => {
  const draft = new ThreadDraft();
  const submit = vi.fn();
  const surface = (shortcut: "enter" | "mod-enter") => (
    <SendShortcutContext value={shortcut}>
      <ComposerEditor
        draft={draft}
        profile={{ display_name: "Alice", color: "#2563eb" }}
        presence={() => {}}
        submit={submit}
      />
    </SendShortcutContext>
  );
  const { rerender } = render(surface("enter"));
  const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
  expect(textbox.getAttribute("aria-keyshortcuts")).toBe("Enter Control+Enter");
  rerender(surface("mod-enter"));
  expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBe(textbox);
  expect(textbox.getAttribute("aria-keyshortcuts")).toBe("Control+Enter");
  expect(textbox.getAttribute("aria-description")).toContain(
    "Ctrl + Enter to send, Enter for a new line.",
  );
  fireEvent.keyDown(textbox, { key: "Enter" });
  fireEvent.keyDown(textbox, { key: "Enter", shiftKey: true });
  expect(draft.doc.getText("text").toString()).toBe("\n\n");
  expect(submit).not.toHaveBeenCalled();
  fireEvent.keyDown(textbox, { key: "Enter", ctrlKey: true });
  expect(submit).toHaveBeenCalledOnce();
});

it.each(["enter", "mod-enter"] as const)(
  "does not send while an IME is composing in %s mode",
  (preference) => {
    const submit = vi.fn();
    render(
      <SendShortcutContext value={preference}>
        <ComposerEditor
          draft={new ThreadDraft()}
          profile={{ display_name: "Alice", color: "#2563eb" }}
          presence={() => {}}
          submit={submit}
        />
      </SendShortcutContext>,
    );
    const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
    fireEvent.keyDown(textbox, {
      key: "Enter",
      isComposing: true,
      ctrlKey: preference === "mod-enter",
    });
    fireEvent.keyDown(textbox, {
      key: "Enter",
      keyCode: 229,
      ctrlKey: preference === "mod-enter",
    });
    fireEvent.compositionStart(textbox);
    fireEvent.keyDown(textbox, {
      key: "Enter",
      ctrlKey: preference === "mod-enter",
    });
    expect(submit).not.toHaveBeenCalled();
    fireEvent.compositionEnd(textbox);
  },
);

it("inserts a new line on a coarse pointer even when Enter normally sends", () => {
  vi.stubGlobal("matchMedia", (query: string) => ({
    matches: query === "(pointer: coarse)",
    addEventListener() {},
    removeEventListener() {},
  }));
  const draft = new ThreadDraft();
  const submit = vi.fn();
  render(
    <SendShortcutContext value="enter">
      <ComposerEditor
        draft={draft}
        profile={{ display_name: "Alice", color: "#2563eb" }}
        presence={() => {}}
        submit={submit}
      />
    </SendShortcutContext>,
  );
  const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
  expect(textbox.getAttribute("enterkeyhint")).toBe("enter");
  expect(textbox.getAttribute("aria-keyshortcuts")).toBe("Control+Enter");
  fireEvent.keyDown(textbox, { key: "Enter" });
  expect(draft.doc.getText("text").toString()).toBe("\n");
  expect(submit).not.toHaveBeenCalled();
});

it("uses the command key and Meta accessibility metadata on Apple platforms", () => {
  vi.spyOn(window.navigator, "platform", "get").mockReturnValue("MacIntel");
  const submit = vi.fn();
  render(
    <SendShortcutContext value="mod-enter">
      <ComposerEditor
        draft={new ThreadDraft()}
        profile={{ display_name: "Alice", color: "#2563eb" }}
        presence={() => {}}
        submit={submit}
      />
    </SendShortcutContext>,
  );
  const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
  expect(textbox.getAttribute("aria-keyshortcuts")).toBe("Meta+Enter");
  expect(textbox.getAttribute("aria-description")).toContain(
    "⌘ + Enter to send",
  );
  fireEvent.keyDown(textbox, { key: "Enter", ctrlKey: true });
  expect(submit).not.toHaveBeenCalled();
  fireEvent.keyDown(textbox, { key: "Enter", metaKey: true });
  expect(submit).toHaveBeenCalledOnce();
});

it.each(["", "Shared"])(
  "renders relative collaborator positions in %j without changing authored text",
  async (text) => {
    const draft = new ThreadDraft();
    draft.doc.getText("text").insert(0, text);
    render(
      <ComposerEditor
        draft={draft}
        profile={{ display_name: "Alice", color: "#2563eb" }}
        presence={() => {}}
        submit={() => {}}
      />,
    );
    const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
    expect(textbox.textContent).toBe(
      text || "Ask anything, or describe a task…",
    );
    expect(draft.doc.getText("text").toString()).toBe(text);
    act(() => {
      draft.participants = {
        "participant-two": {
          name: "Bob",
          color: "#112233",
          anchor: encode(
            Y.encodeRelativePosition(
              Y.createRelativePositionFromTypeIndex(
                draft.doc.getText("text"),
                0,
              ),
            ),
          ),
          head: encode(
            Y.encodeRelativePosition(
              Y.createRelativePositionFromTypeIndex(
                draft.doc.getText("text"),
                Math.min(3, text.length),
              ),
            ),
          ),
        },
      };
      draft.notify();
    });
    await screen.findByText("Bob");
    expect(!!textbox.querySelector(".cm-placeholder")).toBe(!text);
    expect(draft.doc.getText("text").toString()).toBe(text);
    expect([...draft.doc.share.keys()].sort()).toEqual(["attachments", "text"]);
  },
);

function inlineEditor() {
  const draft = new ThreadDraft();
  draft.draftId = "draft-test";
  const editor: { current: EditorView | null } = { current: null };
  const context: ComposerAttachmentView = {
    transport: {
      fetch: vi.fn().mockRejectedValue(new Error("Unavailable")),
    } as unknown as Transport,
    threadId: "thread-one",
    metadata: new Map([
      [
        "attachment-file",
        {
          attachment_id: "attachment-file",
          name: "notes.txt",
          media_type: "text/plain",
          size: 3,
        },
      ],
    ]),
    preview: vi.fn(),
    previewLocal: vi.fn(),
    upload: vi.fn(),
    retry: vi.fn(),
  };
  const rendered = render(
    <ComposerEditor
      draft={draft}
      profile={{ display_name: "Alice", color: "#2563eb" }}
      presence={() => {}}
      submit={() => {}}
      editor={editor}
      attachments={context}
    />,
  );
  return {
    draft,
    editor,
    context,
    rendered,
    textbox: screen.getByRole("textbox", { name: "Shared prompt" }),
  };
}

it("keeps native Pinyin edits in the draft through candidate selection, deletion and further typing", async () => {
  const { draft, editor, textbox } = inlineEditor();
  act(() => {
    draft.doc.getText("text").insert(0, "A");
    editor.current!.dispatch({ selection: { anchor: 1 } });
    editor.current!.focus();
  });
  await act(async () => {});
  const edit = async (value: string, inputType: string) => {
    const text = textbox.querySelector(".cm-line")!.firstChild as Text;
    // A mobile keyboard can signal a key before its native DOM edit is observed.
    fireEvent.keyDown(textbox, { key: "Process", keyCode: 229 });
    text.data = value;
    window.getSelection()!.collapse(text, value.length);
    fireEvent.input(textbox, { inputType });
    await waitFor(() => expect(values(draft.doc).prompt).toBe(value));
  };
  fireEvent.compositionStart(textbox);
  await edit("Ani", "insertCompositionText");
  await edit("A你", "insertCompositionText");
  fireEvent.compositionEnd(textbox, { data: "你" });
  await edit("A", "deleteContentBackward");
  fireEvent.compositionStart(textbox);
  await edit("A好", "insertCompositionText");
  fireEvent.compositionEnd(textbox, { data: "好" });
});

it("moves by Chinese words in the composer without entering attachment tokens or changing the draft", async () => {
  const { draft, editor, textbox } = inlineEditor();
  act(() => {
    draft.doc.getText("text").insert(0, "我们今天讨论中文输入");
    draft.addAttachment("attachment-file", 6);
  });
  await screen.findByText("notes.txt");
  const token = attachmentSelections(draft.doc)[0];
  const original = draft.doc.getText("text").toString();
  act(() => editor.current!.dispatch({ selection: { anchor: 0 } }));
  fireEvent.keyDown(textbox, { key: "ArrowRight", ctrlKey: true });
  expect(editor.current!.state.selection.main.head).toBe(2);
  act(() => editor.current!.dispatch({ selection: { anchor: token.from! } }));
  fireEvent.keyDown(textbox, { key: "ArrowRight", ctrlKey: true });
  expect(editor.current!.state.selection.main.head).toBe(token.to);
  fireEvent.keyDown(textbox, {
    key: "ArrowRight",
    ctrlKey: true,
    shiftKey: true,
  });
  expect(editor.current!.state.selection.main.anchor).toBe(token.to);
  expect(editor.current!.state.selection.main.head).toBe(token.to! + 2);
  act(() => editor.current!.dispatch({ selection: { anchor: token.to! } }));
  fireEvent.keyDown(textbox, { key: "ArrowLeft", ctrlKey: true });
  expect(editor.current!.state.selection.main.head).toBe(token.from);
  expect(draft.doc.getText("text").toString()).toBe(original);
});

it("uses actual CodeMirror atomic deletion and Yjs undo without exposing registry tokens", async () => {
  const { draft, editor, textbox } = inlineEditor();
  act(() => {
    draft.doc.getText("text").insert(0, "before after");
    draft.addAttachment("attachment-file", 7);
  });
  await screen.findByText("notes.txt");
  expect(textbox.textContent).not.toContain("inline-");
  const selection = attachmentSelections(draft.doc)[0];
  act(() => editor.current!.dispatch({ selection: { anchor: selection.to! } }));
  fireEvent.keyDown(textbox, { key: "Backspace" });
  expect(values(draft.doc)).toEqual({
    prompt: "before after",
    attachment_ids: [],
  });
  act(() => draft.undo.undo());
  expect(values(draft.doc).attachment_ids).toEqual(["attachment-file"]);
  await screen.findByText("notes.txt");
  act(() =>
    editor.current!.dispatch({
      changes: { from: 8, to: 10, insert: "replacement" },
      userEvent: "input.type",
    }),
  );
  expect(values(draft.doc)).toEqual({
    prompt: "before replacementafter",
    attachment_ids: [],
  });
});

it.each(["draft-test", undefined])(
  "cuts and pastes genuine identities within draft %s but visible label text stays plain",
  async (draftId) => {
    const { draft, editor, textbox } = inlineEditor();
    draft.draftId = draftId;
    let key = "";
    act(() => {
      key = draft.addAttachment("attachment-file");
    });
    await screen.findByText("notes.txt");
    const data = new Map<string, string>();
    const clipboardData = {
      files: [],
      setData: (type: string, value: string) => data.set(type, value),
      getData: (type: string) => data.get(type) ?? "",
    };
    act(() =>
      editor.current!.dispatch({
        selection: { anchor: 0, head: attachmentToken(key).length },
      }),
    );
    fireEvent.cut(textbox, { clipboardData });
    expect(values(draft.doc).attachment_ids).toEqual([]);
    expect(data.get("text/plain")).toBe("[notes.txt]");
    fireEvent.paste(textbox, { clipboardData });
    expect(values(draft.doc).attachment_ids).toEqual(["attachment-file"]);
    // A second genuine occurrence is distinct: its remove button removes itself.
    fireEvent.paste(textbox, { clipboardData });
    await waitFor(() =>
      expect(
        screen.getAllByRole("button", { name: "Remove notes.txt" }),
      ).toHaveLength(2),
    );
    const before = draft.doc.getText("text").toString();
    fireEvent.click(
      screen.getAllByRole("button", { name: "Remove notes.txt" })[1],
    );
    expect(draft.doc.getText("text").toString()).toBe(
      before.slice(0, attachmentToken(key).length),
    );
    act(() => draft.doc.getText("text").insert(0, "[notes.txt] [image#1]"));
    expect(values(draft.doc).attachment_ids).toEqual(["attachment-file"]);
    expect(values(draft.doc).prompt).toBe("[notes.txt] [image#1]");
  },
);

it("pastes images over the selection and inserts dropped files at the pointer", () => {
  const { draft, editor, context, textbox } = inlineEditor();
  act(() => {
    draft.doc.getText("text").insert(0, "before after");
    editor.current!.dispatch({ selection: { anchor: 9, head: 7 } });
  });
  const file = new File(["image"], "clipboard.png", { type: "image/png" });
  fireEvent.paste(textbox, { clipboardData: { files: [file] } });
  expect(context.upload).toHaveBeenCalledWith([file], 7, 9);
  vi.spyOn(editor.current!, "posAtCoords").mockReturnValue(3);
  fireEvent.drop(textbox, {
    dataTransfer: { files: [file] },
    clientX: 10,
    clientY: 10,
  });
  expect(context.upload).toHaveBeenLastCalledWith([file], 3, 3);
});

it("disposes thumbnail resources even when CodeMirror reuses DOM with an equal new widget", async () => {
  const createObjectURL = vi.fn().mockReturnValue("blob:editor-image");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    },
  );
  try {
    const { draft, editor, context, rendered } = inlineEditor();
    const fetch = vi.fn().mockResolvedValue(new Response("png"));
    context.transport.fetch = fetch;
    context.metadata.set("attachment-image", {
      attachment_id: "attachment-image",
      name: "image.png",
      media_type: "image/png",
      size: 3,
    });
    let key = "";
    act(() => {
      key = draft.addAttachment("attachment-image");
    });
    await waitFor(() => expect(createObjectURL).toHaveBeenCalledTimes(1));
    const image = rendered.container.querySelector('img[alt="image.png"]');
    act(() =>
      editor.current!.dispatch({
        changes: {
          from: 0,
          to: editor.current!.state.doc.length,
          insert: "moved " + attachmentToken(key),
        },
        userEvent: "input.paste",
      }),
    );
    expect(rendered.container.querySelector('img[alt="image.png"]')).toBe(
      image,
    );
    rendered.unmount();
    expect(revokeObjectURL).toHaveBeenCalledWith("blob:editor-image");
    expect(fetch.mock.calls[0][1].signal.aborted).toBe(true);
  } finally {
    vi.unstubAllGlobals();
  }
});

it("clears an idle editor cursor and does not revive it on remote updates or heartbeats", async () => {
  vi.useFakeTimers();
  const focused = vi.spyOn(document, "hasFocus").mockReturnValue(true);
  const presence = vi.fn();
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "Shared");
  const editor: { current: EditorView | null } = { current: null };
  const { unmount } = render(
    <ComposerEditor
      draft={draft}
      editor={editor}
      profile={{ display_name: "Alice", color: "#2563eb" }}
      presence={presence}
      submit={() => {}}
    />,
  );
  try {
    act(() => {
      editor.current!.focus();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30);
    });
    expect(presence.mock.calls.at(-1)![0].anchor).toBeTruthy();
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30000);
    });
    expect(presence.mock.calls.at(-1)![0].anchor).toBeNull();
    presence.mockClear();
    act(() => {
      draft.doc.getText("text").insert(0, "Remote ");
      draft.notify();
    });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(16000);
    });
    expect(
      presence.mock.calls.every(
        ([value]) => value.anchor == null && value.head == null,
      ),
    ).toBe(true);
    fireEvent.keyDown(screen.getByRole("textbox"), { key: "ArrowRight" });
    await act(async () => {
      await vi.advanceTimersByTimeAsync(30);
    });
    expect(presence.mock.calls.at(-1)![0].anchor).toBeTruthy();
    fireEvent.blur(window);
    expect(presence.mock.calls.at(-1)![0].anchor).toBeNull();
  } finally {
    unmount();
    focused.mockRestore();
    vi.useRealTimers();
  }
});

it("renders a file as an atomic attachment with preview, removal, and undo", async () => {
  const { draft, editor, context, textbox } = inlineEditor();
  context.metadata.set("attachment-file", {
    attachment_id: "attachment-file",
    name: "Feedback.txt",
    media_type: "text/plain",
    size: 200,
  });
  act(() => draft.addAttachment("attachment-file"));
  await screen.findByText("Feedback.txt");
  expect(textbox.textContent).not.toContain("inline-");
  fireEvent.click(screen.getByRole("button", { name: "Feedback.txt" }));
  expect(context.preview).toHaveBeenCalledWith(
    "attachment-file",
    context.metadata.get("attachment-file"),
  );
  const token = attachmentSelections(draft.doc)[0];
  act(() => editor.current!.dispatch({ selection: { anchor: token.to! } }));
  fireEvent.keyDown(textbox, { key: "Backspace" });
  expect(values(draft.doc).attachment_ids).toEqual([]);
  act(() => draft.undo.undo());
  await screen.findByText("Feedback.txt");
  expect(values(draft.doc).attachment_ids).toEqual(["attachment-file"]);
});

it("keeps staged images between text slices with local preview, removal and undo", async () => {
  const createObjectURL = vi.fn().mockReturnValue("blob:staged-image");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    },
  );
  const { draft, editor, context, rendered, textbox } = inlineEditor();
  try {
    let key = "";
    const file = new File(["image"], "draft.png", { type: "image/png" });
    act(() => {
      draft.doc.getText("text").insert(0, "before after");
      key = draft.addAttachment("pending", 7);
      draft.uploads.set(key, { file, status: "staged" });
      draft.notify();
    });
    const chip = await screen.findByRole("button", {
      name: "draft.png - ready to upload",
    });
    const image = chip.querySelector("img");
    act(() =>
      editor.current!.dispatch({
        selection: { anchor: 0, head: editor.current!.state.doc.length },
      }),
    );
    expect(chip.parentElement?.hasAttribute("data-selected")).toBe(true);
    act(() => editor.current!.dispatch({ selection: { anchor: 0 } }));
    expect(chip.parentElement?.hasAttribute("data-selected")).toBe(false);
    expect(chip.querySelector("img")).toBe(image);
    expect(chip.textContent).toBe("draft.png");
    expect(textbox.querySelector(".cm-line")?.textContent).toBe(
      "before draft.pngafter",
    );
    fireEvent.click(chip);
    expect(context.previewLocal).toHaveBeenCalledWith(file);
    expect(context.retry).not.toHaveBeenCalled();
    expect(chip.querySelector("img")?.src).toBe("blob:staged-image");
    expect(createObjectURL).toHaveBeenCalledWith(file);
    expect(context.transport.fetch).not.toHaveBeenCalled();
    fireEvent.click(screen.getByRole("button", { name: /Remove draft.png/ }));
    expect(values(draft.doc)).toEqual({
      prompt: "before after",
      attachment_ids: [],
    });
    expect(textbox.contains(window.document.activeElement)).toBe(true);
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:staged-image"),
    );
    act(() => draft.undo.undo());
    await screen.findByRole("button", { name: "draft.png - ready to upload" });
    expect(attachmentSelections(draft.doc)[0].key).toBe(key);
  } finally {
    rendered.unmount();
    vi.unstubAllGlobals();
  }
});

it("keeps the placeholder out of authored input and preserves the editor across context changes", () => {
  const draft = new ThreadDraft();
  const editor = { current: null as EditorView | null };
  const props = {
    draft,
    editor,
    profile: { display_name: "Test", color: "#000000" },
    presence() {},
    submit() {},
  };
  const view = render(<ComposerEditor {...props} />);
  const initial = editor.current;
  expect(screen.getByRole("textbox").textContent).toBe(
    "Ask anything, or describe a task…",
  );
  view.rerender(<ComposerEditor {...props} local />);
  expect(editor.current).toBe(initial);
  expect(screen.getByRole("textbox", { name: "Message" }).textContent).toBe(
    "Ask anything, or describe a task…",
  );
  expect(draft.doc.getText("text").toString()).toBe("");
  act(() => draft.doc.getText("text").insert(0, "Keep this objective"));
  view.rerender(<ComposerEditor {...props} />);
  expect(editor.current).toBe(initial);
  expect(
    screen.getByRole("textbox", { name: "Shared prompt" }).textContent,
  ).toBe("Keep this objective");
  expect(draft.doc.getText("text").toString()).toBe("Keep this objective");
});

it.each([false, true])(
  "keeps Enter with Skill completion and retains the editor across context changes (coarse pointer: %s)",
  async (coarse) => {
    if (coarse) {
      vi.stubGlobal("matchMedia", (query: string) => ({
        matches: query === "(pointer: coarse)",
        addEventListener() {},
        removeEventListener() {},
      }));
    }
    const { startCompletion } = await import("@codemirror/autocomplete");
    const now = vi.spyOn(Date, "now").mockReturnValue(1_000);
    const draft = new ThreadDraft();
    const editor = { current: null as EditorView | null };
    const submit = vi.fn();
    const loadSkills = vi.fn(async () => ({
      catalog_id: "a".repeat(64),
      context_kind: "draft" as const,
      items: [
        {
          item_id: "b".repeat(64),
          name: "review",
          description: "Review carefully",
          source_id: "project",
          logical_path: ".agents/skills/review",
        },
      ],
    }));
    const props = {
      draft,
      editor,
      submit,
      loadSkills,
      profile: { display_name: "Alice", color: "#2563eb" },
      presence: vi.fn(),
    };
    const view = render(<ComposerEditor {...props} skillContext="first" />);
    const original = editor.current;
    act(() => {
      editor.current!.dispatch({
        changes: { from: 0, insert: "Use $rev" },
        selection: { anchor: 8 },
      });
      editor.current!.focus();
      startCompletion(editor.current!);
    });
    await screen.findByRole("option", { name: /Review/ });
    expect(screen.getByText("Review carefully")).toBeTruthy();
    const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
    fireEvent.keyDown(textbox, { key: "Enter" });
    expect(draft.doc.getText("text").toString()).toBe("Use $rev");
    expect(submit).not.toHaveBeenCalled();
    // CodeMirror compares Date.now against the completion's opening timestamp.
    now.mockReturnValue(1_100);
    fireEvent.keyDown(textbox, { key: "Enter" });
    expect(submit).not.toHaveBeenCalled();
    expect(draft.doc.getText("text").toString()).toBe("Use $review ");
    fireEvent.keyDown(textbox, { key: "Backspace" });
    expect(draft.doc.getText("text").toString()).toBe("Use ");
    act(() => draft.undo.undo());
    expect(draft.doc.getText("text").toString()).toBe("Use $review ");
    fireEvent.keyDown(textbox, { key: "Enter", ctrlKey: coarse });
    expect(submit).toHaveBeenCalledOnce();
    view.rerender(<ComposerEditor {...props} skillContext="second" />);
    expect(editor.current).toBe(original);
    expect(draft.doc.getText("text").toString()).toBe("Use $review ");
    act(() => {
      draft.replacement = {
        draft_id: "replacement",
        participant_id: "participant-one",
        participants: {},
        update_base64: encode(Y.encodeStateAsUpdate(new Y.Doc())),
      };
      draft.joinReplacement(false);
    });
    view.rerender(<ComposerEditor {...props} skillContext="second" />);
    expect(editor.current).not.toBe(original);
    act(() => {
      editor.current!.dispatch({
        changes: { from: 0, insert: "$rev" },
        selection: { anchor: 4 },
      });
      editor.current!.focus();
      startCompletion(editor.current!);
    });
    await screen.findByRole("option", { name: /Review/ });
  },
);

it("sends ordinary text without starting skill completion", async () => {
  const { completionStatus } = await import("@codemirror/autocomplete");
  const editor = { current: null as EditorView | null };
  const submit = vi.fn();
  const loadSkills = vi.fn();
  render(
    <ComposerEditor
      draft={new ThreadDraft()}
      editor={editor}
      submit={submit}
      loadSkills={loadSkills}
      profile={{ display_name: "Test", color: "#000000" }}
      presence={() => {}}
    />,
  );
  act(() => {
    editor.current!.focus();
    editor.current!.dispatch({
      changes: { from: 0, insert: "Hello" },
      selection: { anchor: 5 },
      userEvent: "input.type",
    });
    expect(completionStatus(editor.current!.state)).toBeNull();
    fireEvent.keyDown(editor.current!.contentDOM, { key: "Enter" });
  });
  expect(submit).toHaveBeenCalledOnce();
  expect(loadSkills).not.toHaveBeenCalled();
});

it("does not swallow Enter during a skill lookup without visible candidates", async () => {
  const { startCompletion, completionStatus } =
    await import("@codemirror/autocomplete");
  const editor = { current: null as EditorView | null };
  const submit = vi.fn();
  const loadSkills = vi.fn(() => new Promise<never>(() => {}));
  render(
    <ComposerEditor
      draft={new ThreadDraft()}
      editor={editor}
      submit={submit}
      loadSkills={loadSkills}
      profile={{ display_name: "Test", color: "#000000" }}
      presence={() => {}}
    />,
  );
  act(() => {
    editor.current!.focus();
    editor.current!.dispatch({
      changes: { from: 0, insert: "$rev" },
      selection: { anchor: 4 },
    });
    startCompletion(editor.current!);
  });
  await waitFor(() => expect(loadSkills).toHaveBeenCalledOnce());
  expect(completionStatus(editor.current!.state)).toBe("pending");
  expect(screen.queryByRole("listbox")).toBeNull();
  fireEvent.keyDown(editor.current!.contentDOM, { key: "Enter" });
  expect(submit).toHaveBeenCalledOnce();
});

it("focuses when the initial page becomes ready without rebuilding or reclaiming focus on refresh", () => {
  const editor = { current: null as EditorView | null };
  const props = {
    draft: new ThreadDraft(),
    editor,
    submit: vi.fn(),
    presence: vi.fn(),
    profile: { display_name: "Test", color: "#000000" },
  };
  const view = render(
    <>
      <input aria-label="Other field" />
      <ComposerEditor {...props} />
    </>,
  );
  const original = editor.current;
  const other = screen.getByRole("textbox", { name: "Other field" });
  act(() => other.focus());
  view.rerender(
    <>
      <input aria-label="Other field" />
      <ComposerEditor {...props} autoFocus />
    </>,
  );
  expect(document.activeElement).toBe(editor.current!.contentDOM);
  act(() => other.focus());
  view.rerender(
    <>
      <input aria-label="Other field" />
      <ComposerEditor {...props} autoFocus />
    </>,
  );
  expect(document.activeElement).toBe(other);
  expect(editor.current).toBe(original);
});

it("renders manually typed and remote skill references, and discards stale catalogs", async () => {
  const catalog = {
    catalog_id: "a".repeat(64),
    context_kind: "draft" as const,
    items: [
      {
        item_id: "b".repeat(64),
        name: "review",
        description: "Review",
        source_id: "project",
        logical_path: "/skills/review",
      },
    ],
  };
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "😀 $review $unknown");
  const editor = { current: null as EditorView | null };
  let resolveOld!: (value: typeof catalog) => void;
  const props = {
    draft,
    editor,
    submit: vi.fn(),
    presence: vi.fn(),
    profile: { display_name: "A", color: "#2563eb" },
  };
  const page = render(
    <ComposerEditor
      {...props}
      skillContext="old"
      loadSkills={() =>
        new Promise((resolve) => {
          resolveOld = resolve;
        })
      }
    />,
  );
  const original = editor.current;
  page.rerender(
    <ComposerEditor
      {...props}
      skillContext="new"
      loadSkills={async () => ({ ...catalog, items: [] })}
    />,
  );
  await act(async () => {
    resolveOld(catalog);
  });
  expect(page.container.querySelector("[data-skill]")).toBeNull();
  page.rerender(
    <ComposerEditor
      {...props}
      skillContext="available"
      loadSkills={async () => catalog}
    />,
  );
  await waitFor(() =>
    expect(
      page.container.querySelector('[data-skill="review"]')?.textContent,
    ).toBe("Review"),
  );
  expect(editor.current).toBe(original);
  expect(draft.doc.getText("text").toString()).toBe("😀 $review $unknown");
  act(() => {
    editor.current!.dispatch({
      changes: { from: 9, to: 10 },
      selection: { anchor: 9 },
    });
  });
  expect(draft.doc.getText("text").toString()).toBe("😀 $revie $unknown");
  await waitFor(() =>
    expect(page.container.querySelector("[data-skill]")).toBeNull(),
  );
  act(() => {
    draft.doc.getText("text").insert(9, "w");
  });
  await waitFor(() =>
    expect(
      page.container.querySelector('[data-skill="review"]'),
    ).not.toBeNull(),
  );
});

it("opens skill suggestions without a typing debounce and filters each edit synchronously", async () => {
  const { currentCompletions, completionStatus } =
    await import("@codemirror/autocomplete");
  const submit = vi.fn();
  const editor = { current: null as EditorView | null };
  const loadSkills = vi.fn(async () => ({
    catalog_id: "catalog",
    context_kind: "draft" as const,
    items: ["review", "release"].map((name) => ({
      item_id: name,
      name,
      description: name,
      source_id: "project",
      logical_path: `/skills/${name}`,
    })),
  }));
  vi.useFakeTimers({ toFake: ["setTimeout", "clearTimeout", "Date"] });
  try {
    render(
      <ComposerEditor
        draft={new ThreadDraft()}
        editor={editor}
        submit={submit}
        loadSkills={loadSkills}
        profile={{ display_name: "Test", color: "#000000" }}
        presence={() => {}}
      />,
    );
    act(() => {
      editor.current!.focus();
      editor.current!.dispatch({
        changes: { from: 0, insert: "$re" },
        selection: { anchor: 3 },
        userEvent: "input.type",
      });
    });
    // Exercise automatic activation, not startCompletion's explicit shortcut.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(50);
    });
    const labels = () =>
      currentCompletions(editor.current!.state).map((item) => item.label);
    expect(completionStatus(editor.current!.state)).toBe("active");
    expect(labels()).toEqual(["$release", "$review"]);
    const requests = loadSkills.mock.calls.length;
    act(() => {
      editor.current!.dispatch({
        changes: { from: 3, insert: "v" },
        selection: { anchor: 4 },
        userEvent: "input.type",
      });
    });
    expect(labels()).toEqual(["$review"]);
    expect(completionStatus(editor.current!.state)).toBe("active");
    act(() => {
      editor.current!.dispatch({
        changes: { from: 3, to: 4 },
        selection: { anchor: 3 },
        userEvent: "delete.backward",
      });
    });
    expect(labels()).toEqual(["$release", "$review"]);
    // A pause beyond the query cache's stale time must not reload this token.
    await act(async () => {
      await vi.advanceTimersByTimeAsync(11000);
    });
    act(() => {
      editor.current!.dispatch({
        changes: { from: 3, insert: "l" },
        selection: { anchor: 4 },
        userEvent: "input.type",
      });
    });
    expect(labels()).toEqual(["$release"]);
    expect(loadSkills).toHaveBeenCalledTimes(requests);
    fireEvent.keyDown(editor.current!.contentDOM, { key: "Escape" });
    const type = async (text: string) => {
      await act(async () => {
        const view = editor.current!;
        view.dispatch(view.state.replaceSelection(text), {
          userEvent: "input.type",
        });
        await vi.advanceTimersByTimeAsync(100);
      });
    };
    await type("e");
    expect(editor.current!.state.doc.toString()).toBe("$rele");
    expect(labels()).toEqual([]);
    await type(" /");
    expect(labels()).toEqual(["$release", "$review"]);
    await type("tmp");
    expect(labels()).toEqual([]);
    fireEvent.keyDown(editor.current!.contentDOM, { key: "Enter" });
    expect(submit).toHaveBeenCalledOnce();
  } finally {
    cleanup();
    vi.useRealTimers();
  }
});

it("keeps a prediction out of shared text until Tab fills it as one undoable edit", () => {
  const draft = new ThreadDraft();
  const submit = vi.fn();
  const dismiss = vi.fn();
  const text =
    "Add an example for each usage scenario and keep the commands runnable.\nInclude the expected output and note where to find the full logs.";
  render(
    <ComposerEditor
      draft={draft}
      profile={{ display_name: "Alice", color: "#2563eb" }}
      presence={() => {}}
      submit={submit}
      prediction={{ text, dismiss }}
    />,
  );
  const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
  expect(textbox.querySelector(".cm-placeholder")?.textContent).toBe(text);
  expect(textbox.contains(screen.getByText("Tab"))).toBe(false);
  expect(textbox.getAttribute("aria-description")).toContain(
    `Suggested follow-up: ${text} Press Tab to fill or Escape to dismiss.`,
  );
  expect(draft.doc.getText("text").length).toBe(0);
  expect(draft.undo.undoStack).toHaveLength(0);
  fireEvent.keyDown(textbox, { key: "Enter" });
  expect(submit).not.toHaveBeenCalled();
  fireEvent.keyDown(textbox, { key: "Escape" });
  expect(dismiss).toHaveBeenCalledTimes(1);
  fireEvent.keyDown(textbox, { key: "Tab" });
  expect(draft.doc.getText("text").toString()).toBe(text);
  expect(textbox.getAttribute("aria-description")).not.toContain(
    "Suggested follow-up:",
  );
  expect(draft.undo.undoStack).toHaveLength(1);
  act(() => draft.undo.undo());
  expect(draft.doc.getText("text").length).toBe(0);
});

it("does not accept a ghost prediction during IME composition", () => {
  const draft = new ThreadDraft();
  render(
    <ComposerEditor
      draft={draft}
      profile={{ display_name: "Alice", color: "#2563eb" }}
      presence={() => {}}
      submit={() => {}}
      prediction={{ text: "Add an example.", dismiss: () => {} }}
    />,
  );
  const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
  fireEvent.compositionStart(textbox);
  fireEvent.keyDown(textbox, { key: "Tab" });
  expect(draft.doc.getText("text").length).toBe(0);
  expect(draft.undo.undoStack).toHaveLength(0);
});

it("offers clickable accept and dismiss controls outside the textbox", () => {
  const draft = new ThreadDraft();
  const dismiss = vi.fn();
  render(
    <ComposerEditor
      draft={draft}
      profile={{ display_name: "Alice", color: "#2563eb" }}
      presence={() => {}}
      submit={() => {}}
      prediction={{ text: "Add an example.", dismiss }}
    />,
  );
  const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
  const useSuggestion = screen.getByRole("button", { name: "Use suggestion" });
  const dismissSuggestion = screen.getByRole("button", {
    name: "Dismiss suggestion",
  });
  expect(textbox.contains(useSuggestion)).toBe(false);
  expect(useSuggestion.contains(screen.getByText("Tab"))).toBe(true);
  expect(textbox.contains(dismissSuggestion)).toBe(false);
  fireEvent.click(dismissSuggestion);
  expect(dismiss).toHaveBeenCalledTimes(1);
  fireEvent.click(useSuggestion);
  expect(draft.doc.getText("text").toString()).toBe("Add an example.");
  expect(document.activeElement).toBe(textbox);
});

it("completes an ACP slash command without submitting the prompt", async () => {
  const draft = new ThreadDraft();
  const submit = vi.fn();
  const editor = { current: null as EditorView | null };
  render(
    <ComposerEditor
      draft={draft}
      profile={{ display_name: "Alice", color: "#2563eb" }}
      presence={() => {}}
      submit={submit}
      editor={editor}
      autoFocus
      acpCommands={[
        { name: "compact", description: "Compact the conversation" },
      ]}
    />,
  );
  act(() =>
    editor.current!.dispatch({
      changes: { from: 0, insert: "/" },
      selection: { anchor: 1 },
      userEvent: "input.type",
    }),
  );
  await screen.findByRole("option", { name: /\/compact/ });
  expect(screen.getByText("Command")).toBeTruthy();
  await waitFor(() =>
    expect(
      screen
        .getByRole("option", { name: /\/compact/ })
        .getAttribute("aria-selected"),
    ).toBe("true"),
  );
  const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
  await waitFor(() => {
    // CodeMirror briefly ignores acceptCompletion when the menu first appears.
    expect(submit).not.toHaveBeenCalled();
    fireEvent.keyDown(textbox, { key: "Enter" });
    expect(values(draft.doc).prompt).toBe("/compact ");
  });
  expect(submit).not.toHaveBeenCalled();
});

it("merges peer ACP commands and Harness UI skills on slash and shows skills on dollar", async () => {
  const draft = new ThreadDraft();
  const submit = vi.fn();
  const editor = { current: null as EditorView | null };
  const loadSkills = vi.fn(async () => ({
    catalog_id: "a".repeat(64),
    context_kind: "idle" as const,
    items: [
      {
        item_id: "b".repeat(64),
        name: "plan",
        description: "Project planning workflow",
        source_id: "a13n-harness-ui:project:workspace",
        logical_path: "/repo/.agents/skills/plan",
      },
      {
        item_id: "c".repeat(64),
        name: "worktree-pr",
        description: "Open a PR from a fresh worktree",
        source_id: "a13n-harness-ui:builtin-skills",
        logical_path: "/environment/builtin-skills/worktree-pr",
      },
    ],
  }));
  render(
    <ComposerEditor
      draft={draft}
      profile={{ display_name: "Alice", color: "#2563eb" }}
      presence={() => {}}
      submit={submit}
      editor={editor}
      autoFocus
      loadSkills={loadSkills}
      acpCommands={[
        { name: "compact", description: "Compact conversation" },
        { name: "plan", description: "Peer plan mode" },
      ]}
    />,
  );
  act(() =>
    editor.current!.dispatch({
      changes: { from: 0, insert: "/" },
      selection: { anchor: 1 },
      userEvent: "input.type",
    }),
  );
  await screen.findByRole("option", { name: /\/compact/ });
  await screen.findByRole("option", { name: /\/plan/ });
  await screen.findByRole("option", { name: /Plan \(\$plan\)/ });
  await screen.findByRole("option", { name: /Worktree Pr/ });
  expect(screen.getAllByText("Command")).toHaveLength(2);
  expect(screen.getByText("Project")).toBeTruthy();
  expect(screen.getByText("Built-in")).toBeTruthy();

  act(() => {
    editor.current!.dispatch({
      changes: { from: 0, to: 1 },
      selection: { anchor: 0 },
      userEvent: "delete.backward",
    });
    editor.current!.dispatch({
      changes: { from: 0, insert: "$" },
      selection: { anchor: 1 },
      userEvent: "input.type",
    });
  });
  await waitFor(() =>
    expect(screen.queryByRole("option", { name: /\/compact/ })).toBeNull(),
  );
  await screen.findByRole("option", { name: /Worktree Pr/ });
  expect(screen.queryByRole("option", { name: /\/plan/ })).toBeNull();
});
