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
afterEach(cleanup);
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
it("does not submit Enter while an IME is composing", () => {
  const submit = vi.fn();
  render(
    <ComposerEditor
      draft={new ThreadDraft()}
      profile={{ display_name: "Alice", color: "#2563eb" }}
      presence={() => {}}
      submit={submit}
    />,
  );
  const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
  fireEvent.compositionStart(textbox);
  fireEvent.keyDown(textbox, { key: "Enter", isComposing: true, keyCode: 229 });
  fireEvent.keyDown(textbox, {
    key: "Enter",
    ctrlKey: true,
    isComposing: true,
  });
  expect(submit).not.toHaveBeenCalled();
  fireEvent.compositionEnd(textbox);
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
    expect(textbox.textContent).toBe(text);
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
    expect(textbox.querySelector(".cm-placeholder")).toBeNull();
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

it("routes clipboard images and drops to the current insertion position", () => {
  const { draft, editor, context, textbox } = inlineEditor();
  act(() => {
    draft.doc.getText("text").insert(0, "before after");
    editor.current!.dispatch({ selection: { anchor: 7 } });
  });
  const file = new File(["image"], "clipboard.png", { type: "image/png" });
  fireEvent.paste(textbox, { clipboardData: { files: [file] } });
  expect(context.upload).toHaveBeenCalledWith([file], 7);
  vi.spyOn(editor.current!, "posAtCoords").mockReturnValue(3);
  fireEvent.drop(textbox, {
    dataTransfer: { files: [file] },
    clientX: 10,
    clientY: 10,
  });
  expect(context.upload).toHaveBeenLastCalledWith([file], 3);
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

it("renders a comment as the same atomic attachment with preview, removal, and undo", async () => {
  const { draft, editor, context, textbox } = inlineEditor();
  context.metadata.set("attachment-comment", {
    attachment_id: "attachment-comment",
    name: "Feedback by Reader.txt",
    media_type: "text/plain",
    size: 200,
    source: {
      kind: "comment_reference",
      root_thread_id: "thread-one",
      comment_id: "comment-1234567890123456",
      target: {
        producing_thread_id: "thread-one",
        source_id: "a".repeat(64),
        location: { kind: "root_text", message: 0, part: 0 },
      },
    },
    comment: {
      version: 2,
      author: "Reader",
      preview: "Please reconsider the conclusion",
      quote: "saved source",
    },
  });
  act(() => draft.addAttachment("attachment-comment"));
  await screen.findByText("Comment · Reader");
  expect(screen.getByText("Please reconsider the conclusion")).toBeTruthy();
  expect(textbox.textContent).not.toContain("comment-123");
  expect(textbox.textContent).not.toContain("inline-");
  fireEvent.click(
    screen.getByRole("button", { name: "Feedback by Reader.txt" }),
  );
  expect(context.preview).toHaveBeenCalledWith(
    "attachment-comment",
    context.metadata.get("attachment-comment"),
  );
  const token = attachmentSelections(draft.doc)[0];
  act(() => editor.current!.dispatch({ selection: { anchor: token.to! } }));
  fireEvent.keyDown(textbox, { key: "Backspace" });
  expect(values(draft.doc).attachment_ids).toEqual([]);
  act(() => draft.undo.undo());
  await screen.findByText("Comment · Reader");
  expect(values(draft.doc).attachment_ids).toEqual(["attachment-comment"]);
});

it("previews staged images locally and releases the thumbnail when removed", async () => {
  const createObjectURL = vi.fn().mockReturnValue("blob:staged-image");
  const revokeObjectURL = vi.fn();
  vi.stubGlobal(
    "URL",
    class extends URL {
      static createObjectURL = createObjectURL;
      static revokeObjectURL = revokeObjectURL;
    },
  );
  const { draft, context, rendered } = inlineEditor();
  try {
    let key = "";
    const file = new File(["image"], "draft.png", { type: "image/png" });
    act(() => {
      key = draft.addAttachment("pending");
      draft.uploads.set(key, { file, status: "staged" });
      draft.notify();
    });
    const chip = await screen.findByRole("button", {
      name: "draft.png · ready to upload",
    });
    expect(chip.textContent).toBe("draft.png");
    expect(chip.querySelector("img")?.src).toBe("blob:staged-image");
    expect(createObjectURL).toHaveBeenCalledWith(file);
    expect(context.transport.fetch).not.toHaveBeenCalled();
    act(() => draft.removeAttachment(key));
    await waitFor(() =>
      expect(revokeObjectURL).toHaveBeenCalledWith("blob:staged-image"),
    );
  } finally {
    rendered.unmount();
    vi.unstubAllGlobals();
  }
});

it("changes draft-lifetime guidance without remounting the editor on Thread creation", () => {
  const draft = new ThreadDraft();
  const props = {
    draft,
    profile: { display_name: "Test", color: "#000000" },
    presence() {},
    submit() {},
  };
  const view = render(<ComposerEditor {...props} local />);
  const editor = screen.getByRole("textbox", { name: "Message" });
  expect(editor.hasAttribute("data-composer-editor")).toBe(true);
  expect(editor.getAttribute("aria-description")).toContain(
    "saved in this browser when storage is available",
  );
  expect(editor.getAttribute("aria-description")).toContain(
    "Local files may need reattaching",
  );
  expect(editor.getAttribute("aria-description")).toContain(
    "Enter to send; Shift+Enter",
  );
  view.rerender(<ComposerEditor {...props} local={false} />);
  expect(screen.getByRole("textbox", { name: "Shared prompt" })).toBe(editor);
  expect(editor.hasAttribute("data-composer-editor")).toBe(true);
  expect(editor.getAttribute("aria-description")).toContain(
    "Shared with this conversation",
  );
});

it("keeps an empty editor blank and preserves input when its accessible context changes", () => {
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
  expect(screen.getByRole("textbox").textContent).toBe("");
  view.rerender(<ComposerEditor {...props} local />);
  expect(editor.current).toBe(initial);
  expect(screen.getByRole("textbox", { name: "Message" }).textContent).toBe("");
  expect(draft.doc.getText("text").toString()).toBe("");
  act(() => draft.doc.getText("text").insert(0, "Keep this objective"));
  view.rerender(<ComposerEditor {...props} />);
  expect(editor.current).toBe(initial);
  expect(
    screen.getByRole("textbox", { name: "Shared prompt" }).textContent,
  ).toBe("Keep this objective");
  expect(draft.doc.getText("text").toString()).toBe("Keep this objective");
});

it("completes dollar skills without sending, and retains the editor across catalog context changes", async () => {
  const { startCompletion } = await import("@codemirror/autocomplete");
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
  await screen.findByRole("option", { name: /\$review/ });
  expect(screen.getByText("Review carefully")).toBeTruthy();
  // CodeMirror suppresses accidental acceptance immediately after opening.
  await act(async () => {
    await new Promise((resolve) => setTimeout(resolve, 100));
  });
  const textbox = screen.getByRole("textbox", { name: "Shared prompt" });
  fireEvent.keyDown(textbox, { key: "Enter" });
  expect(submit).not.toHaveBeenCalled();
  expect(draft.doc.getText("text").toString()).toBe("Use $review ");
  fireEvent.keyDown(textbox, { key: "Enter" });
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
  await screen.findByRole("option", { name: /\$review/ });
});

it("sends immediately after ordinary typing even while completion checks are pending", async () => {
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
    expect(completionStatus(editor.current!.state)).toBe("pending");
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
