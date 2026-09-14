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
  expect(submit).not.toHaveBeenCalled();
  fireEvent.keyDown(textbox, { key: "Enter", ctrlKey: true });
  expect(submit).toHaveBeenCalledTimes(1);
});
it("renders relative collaborator positions without sending them as CRDT roots", async () => {
  const draft = new ThreadDraft();
  draft.doc.getText("text").insert(0, "Shared");
  render(
    <ComposerEditor
      draft={draft}
      profile={{ display_name: "Alice", color: "#2563eb" }}
      presence={() => {}}
      submit={() => {}}
    />,
  );
  act(() => {
    draft.participants = {
      "participant-two": {
        name: "Bob",
        color: "#112233",
        anchor: encode(
          Y.encodeRelativePosition(
            Y.createRelativePositionFromTypeIndex(draft.doc.getText("text"), 0),
          ),
        ),
        head: encode(
          Y.encodeRelativePosition(
            Y.createRelativePositionFromTypeIndex(draft.doc.getText("text"), 3),
          ),
        ),
      },
    };
    draft.notify();
  });
  await screen.findByText("Bob");
  expect([...draft.doc.share.keys()].sort()).toEqual(["attachments", "text"]);
});

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

it("cuts and pastes genuine identities within the draft but visible label text stays plain", async () => {
  const { draft, editor, textbox } = inlineEditor();
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
});

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
