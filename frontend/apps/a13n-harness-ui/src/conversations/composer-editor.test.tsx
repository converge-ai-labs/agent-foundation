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
import { ThreadDraft, encode } from "./draft";

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
