// @vitest-environment jsdom
import { createRef } from "react";
import { afterEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
  waitFor,
} from "@testing-library/react";
import userEvent from "@testing-library/user-event";
import { TransportContext } from "../transport/context";
import type { Transport } from "../transport/client";
import { ThreadDraft } from "./draft";
import { ReplyAnnotations } from "./reply-annotations";
import { recordAudio } from "./dictation-audio";

vi.mock("./dictation-audio", () => ({
  recordAudio: vi.fn(async () => async () => new Blob(["sample"])),
  encodeAudio: async () => "recorded-audio",
}));
afterEach(() => {
  window.getSelection()?.removeAllRanges();
  cleanup();
});

it("keeps comment dictation and its retry attached to the quote when working elsewhere", async () => {
  Object.defineProperty(Range.prototype, "getClientRects", {
    configurable: true,
    value: () => [],
  });
  const user = userEvent.setup();
  const reader = createRef<HTMLDivElement>();
  const draft = new ThreadDraft();
  let fail!: (error: Error) => void;
  let succeed!: (result: unknown) => void;
  const post = vi
    .fn()
    .mockImplementationOnce(
      () =>
        new Promise((_, reject) => {
          fail = reject;
        }),
    )
    .mockImplementationOnce(
      () =>
        new Promise((resolve) => {
          succeed = resolve;
        }),
    );
  render(
    <TransportContext
      value={{ client: { POST: post } } as unknown as Transport}
    >
      <ReplyAnnotations
        draft={draft}
        reader={reader}
        dictation={{ words: 4 }}
        enabled
      >
        <div ref={reader}>
          <p data-reply-source="reply">Review this change</p>
        </div>
        <input aria-label="Other work" />
      </ReplyAnnotations>
    </TransportContext>,
  );
  const range = document.createRange();
  range.selectNodeContents(screen.getByText("Review this change"));
  window.getSelection()!.addRange(range);
  fireEvent(document, new Event("selectionchange"));
  await user.click(await screen.findByRole("button", { name: "Add to chat" }));
  await user.click(screen.getByRole("button", { name: "Voice input" }));
  const other = screen.getByRole("textbox", { name: "Other work" });
  await user.type(other, "a");
  await user.click(screen.getByRole("button", { name: "Stop recording" }));
  await user.type(other, "b");
  await act(() => fail(new Error("Unavailable")));
  expect(screen.getByRole("alert").textContent).toBe("Transcription failed.");
  await user.click(screen.getByRole("button", { name: "Retry" }));
  await user.type(other, "c");
  await act(() => succeed({ data: { text: "Looks good." } }));
  await waitFor(() =>
    expect(
      screen.getByRole("textbox", { name: "Annotation comment" }),
    ).toHaveProperty("value", "Looks good."),
  );
  await act(
    () => new Promise<void>((done) => requestAnimationFrame(() => done())),
  );
  expect(document.activeElement).toBe(other);
  expect(other).toHaveProperty("value", "abc");
  expect(recordAudio).toHaveBeenCalledTimes(1);
  await user.click(screen.getByRole("button", { name: "Add annotation" }));
  expect(draft.annotations).toMatchObject([
    { text: "Review this change", comment: "Looks good." },
  ]);
});

it("leaves composing Enter and WebKit's 229 confirmation to the IME", async () => {
  const reader = createRef<HTMLDivElement>();
  const draft = new ThreadDraft();
  const user = userEvent.setup();
  render(
    <ReplyAnnotations draft={draft} reader={reader} enabled>
      <div ref={reader}>
        <p data-reply-source="reply">Review this change</p>
      </div>
    </ReplyAnnotations>,
  );
  const range = document.createRange();
  range.selectNodeContents(screen.getByText("Review this change"));
  window.getSelection()!.addRange(range);
  fireEvent(document, new Event("selectionchange"));
  await user.click(await screen.findByRole("button", { name: "Add to chat" }));
  const comment = screen.getByRole("textbox", { name: "Annotation comment" });
  fireEvent.change(comment, { target: { value: "確認" } });
  for (const ime of [{ isComposing: true }, { keyCode: 229 }]) {
    expect(fireEvent.keyDown(comment, { key: "Enter", ...ime })).toBe(true);
  }
  expect(draft.annotations).toEqual([]);
  expect(screen.getByRole("textbox", { name: "Annotation comment" })).toBe(
    comment,
  );
  fireEvent.keyDown(comment, { key: "Enter" });
  expect(draft.annotations).toMatchObject([{ comment: "確認" }]);
});
