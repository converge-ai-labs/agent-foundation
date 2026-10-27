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
import type { ComponentProps } from "react";
import AnnotationEditor from "./annotation-editor";
import type { AnnotationCanvas } from "./annotation-canvas";
import {
  copyAnnotation,
  downloadAnnotation,
  exportAnnotation,
} from "./annotation-export";

vi.mock("./annotation-export", () => ({
  copyAnnotation: vi.fn(),
  downloadAnnotation: vi.fn(),
  exportAnnotation: vi.fn(),
}));
// Canvas rendering has a real-browser fixture; these tests exercise editor state and UI.
vi.mock("./annotation-canvas", () => ({
  AnnotationCanvas: (props: ComponentProps<typeof AnnotationCanvas>) => {
    props.contentRef.current = {} as NonNullable<
      typeof props.contentRef.current
    >;
    return (
      <div>
        <button onClick={() => props.onStart({ x: 30, y: 40 })}>
          Start mark
        </button>
        <button onClick={() => props.onMove({ x: 130, y: 140 }, false)}>
          Move mark
        </button>
        <button onClick={props.onEnd}>End mark</button>
        <button onClick={props.onCancel}>Cancel gesture</button>
        <output data-testid="document">{JSON.stringify(props.document)}</output>
        <output data-testid="scale">{props.scale}</output>
      </div>
    );
  },
}));
const observe = vi.fn(),
  disconnect = vi.fn();
beforeEach(() => {
  vi.stubGlobal(
    "ResizeObserver",
    class {
      observe = observe;
      disconnect = disconnect;
    },
  );
  vi.spyOn(HTMLElement.prototype, "clientWidth", "get").mockReturnValue(448);
  vi.spyOn(HTMLElement.prototype, "clientHeight", "get").mockReturnValue(248);
  const computed = window.getComputedStyle;
  vi.spyOn(window, "getComputedStyle").mockImplementation((element) => {
    const style = computed(element);
    style.padding = "24px";
    return style;
  });
  vi.mocked(exportAnnotation).mockResolvedValue(new Blob(["PNG"]));
  vi.mocked(copyAnnotation).mockResolvedValue(undefined);
  vi.mocked(downloadAnnotation).mockResolvedValue(undefined);
});
afterEach(() => {
  cleanup();
  vi.restoreAllMocks();
  vi.unstubAllGlobals();
  vi.clearAllMocks();
});
const click = (name: string) =>
  fireEvent.click(screen.getByRole("button", { name }));
const state = () => JSON.parse(screen.getByTestId("document").textContent!);
async function show(clipboardReady = Promise.resolve()) {
  const onClose = vi.fn(),
    onCopied = vi.fn();
  const source = document.createElement("canvas");
  source.width = 800;
  source.height = 400;
  const view = render(
    <AnnotationEditor
      source={source}
      clipboardReady={clipboardReady}
      onClose={onClose}
      onCopied={onCopied}
    />,
  );
  await screen.findByRole("dialog");
  return { ...view, onClose, onCopied };
}
it("measures the portalled viewport and commits gestures as single undoable edits", async () => {
  const view = await show();
  await waitFor(() =>
    expect(screen.getByTestId("scale").textContent).toBe("0.5"),
  );
  expect(observe).toHaveBeenCalled();
  click("Start mark");
  click("Move mark");
  click("Cancel gesture");
  expect(state().annotations).toHaveLength(0);
  click("Start mark");
  click("Move mark");
  click("End mark");
  expect(state().annotations).toHaveLength(1);
  click("Undo (Ctrl/⌘ Z)");
  expect(state().annotations).toHaveLength(0);
  click("Redo (Ctrl/⌘ Shift Z)");
  expect(state().annotations).toHaveLength(1);
  view.unmount();
  expect(disconnect).toHaveBeenCalled();
});
it("keeps native multiline composition separate from document history", async () => {
  const { onClose } = await show();
  click("Text (T)");
  click("Start mark");
  const input = screen.getByRole("textbox", { name: "Annotation text" });
  await waitFor(() => expect(document.activeElement).toBe(input));
  expect(
    screen.getByText(/Editing text · Ctrl\/⌘ Enter to apply/),
  ).toBeTruthy();
  fireEvent.compositionStart(input);
  fireEvent.change(input, { target: { value: "中文\nsecond line" } });
  fireEvent.keyDown(input, { key: "Enter", ctrlKey: true, isComposing: true });
  expect(state().annotations).toHaveLength(0);
  fireEvent.compositionEnd(input);
  fireEvent.keyDown(input, { key: "Enter", ctrlKey: true });
  expect(state().annotations[0].text).toBe("中文\nsecond line");
  click("Edit text");
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "discard" },
  });
  fireEvent.keyDown(screen.getByRole("textbox"), { key: "Escape" });
  expect(state().annotations[0].text).toBe("中文\nsecond line");
  expect(onClose).not.toHaveBeenCalled();
  click("Delete annotation");
  expect(state().annotations).toHaveLength(0);
  click("Undo (Ctrl/⌘ Z)");
  expect(state().annotations[0].text).toBe("中文\nsecond line");
});
it.each(["close", "escape"])(
  "confirms %s without losing edits or undo history",
  async (method) => {
    const { onClose } = await show();
    click("Start mark");
    click("Move mark");
    click("End mark");
    if (method === "close") click("Close annotation");
    else fireEvent.keyDown(screen.getByRole("dialog"), { key: "Escape" });
    await screen.findByRole("dialog", { name: "Discard annotations?" });
    expect(onClose).not.toHaveBeenCalled();
    click("Keep editing");
    expect(state().annotations).toHaveLength(1);
    click("Undo (Ctrl/⌘ Z)");
    expect(state().annotations).toHaveLength(0);
    // Redo history is local work too, even when the current image is unchanged.
    click("Close annotation");
    await screen.findByRole("dialog", { name: "Discard annotations?" });
    click("Discard");
    expect(onClose).toHaveBeenCalledOnce();
  },
);
it("preserves an unapplied text draft when closing is cancelled", async () => {
  const { onClose } = await show();
  click("Text (T)");
  click("Start mark");
  fireEvent.change(screen.getByRole("textbox"), {
    target: { value: "Keep this draft" },
  });
  click("Close annotation");
  await screen.findByRole("dialog", { name: "Discard annotations?" });
  click("Keep editing");
  expect((screen.getByRole("textbox") as HTMLTextAreaElement).value).toBe(
    "Keep this draft",
  );
  click("Apply text (Ctrl/⌘ Enter)");
  expect(state().annotations[0].text).toBe("Keep this draft");
  expect(onClose).not.toHaveBeenCalled();
});
it("closes an untouched capture without confirmation", async () => {
  const { onClose } = await show();
  click("Close annotation");
  expect(onClose).toHaveBeenCalledOnce();
  expect(screen.queryByText("Discard annotations?")).toBeNull();
});
it("focuses Copy after clipboard readiness without stealing an active tool's focus", async () => {
  let ready!: () => void;
  await show(
    new Promise<void>((resolve) => {
      ready = resolve;
    }),
  );
  const popup = screen.getByRole("dialog");
  await waitFor(() => expect(document.activeElement).toBe(popup));
  const tool = screen.getByRole("button", { name: "Text (T)" });
  tool.focus();
  await act(async () => ready());
  expect(document.activeElement).toBe(tool);
});
it("initially focuses Copy rather than Download", async () => {
  await show();
  await waitFor(() =>
    expect(document.activeElement).toBe(
      screen.getByRole("button", { name: "Copy PNG (Ctrl/⌘ Enter)" }),
    ),
  );
});
it("waits for text writes, preserves edits on failed copy, and offers download or retry", async () => {
  let ready!: () => void;
  const { onCopied } = await show(
    new Promise<void>((resolve) => {
      ready = resolve;
    }),
  );
  expect(
    (
      screen.getByRole("button", {
        name: "Copy PNG (Ctrl/⌘ Enter)",
      }) as HTMLButtonElement
    ).disabled,
  ).toBe(true);
  await act(async () => ready());
  click("Start mark");
  click("Move mark");
  click("End mark");
  vi.mocked(copyAnnotation).mockRejectedValueOnce(
    new Error("permission denied"),
  );
  click("Copy PNG (Ctrl/⌘ Enter)");
  await screen.findByText(/Could not copy/);
  expect(state().annotations).toHaveLength(1);
  expect(onCopied).not.toHaveBeenCalled();
  click("Download PNG");
  await screen.findByText("PNG downloaded.");
  expect(downloadAnnotation).toHaveBeenCalledOnce();
  click("Copy PNG (Ctrl/⌘ Enter)");
  await waitFor(() => expect(onCopied).toHaveBeenCalledOnce());
});
