// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import { act, cleanup, render, screen } from "@testing-library/react";
import { EditorView } from "codemirror";
import { SourceEditor } from "./editor-view";

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
it("the actual CodeMirror editor does not turn native CRLF reads into edits and preserves CRLF when typing", () => {
  const change = vi.fn();
  const rendered = render(
    <SourceEditor
      value={"first\r\nsecond\r\n"}
      onChange={change}
      language="plain"
      label="Native text"
    />,
  );
  const element = screen.getByRole("textbox", { name: "Native text" });
  expect(change).not.toHaveBeenCalled();
  const editor = EditorView.findFromDOM(element)!;
  act(() => editor.dispatch({ changes: { from: 0, insert: "edited " } }));
  expect(change).toHaveBeenLastCalledWith("edited first\r\nsecond\r\n");
  change.mockClear();
  rendered.rerender(
    <SourceEditor
      value={"external\r\nrevision\r\n"}
      onChange={change}
      language="plain"
      label="Native text"
    />,
  );
  expect(change).not.toHaveBeenCalled();
  expect(element.textContent).toContain("external");
});
it("mixed-line-ending files and read-only previews never publish a synthetic initial edit", () => {
  const change = vi.fn();
  render(
    <SourceEditor
      value={"first\r\nsecond\nthird\r"}
      onChange={change}
      language="plain"
      readOnly
      label="Mixed native text"
    />,
  );
  expect(change).not.toHaveBeenCalled();
  const editor = EditorView.findFromDOM(
    screen.getByLabelText("Mixed native text"),
  )!;
  expect(editor.state.readOnly).toBe(true);
});

it("keeps selection and editor identity across wrapping, jumps to a line, and remembers position", () => {
  const remember = vi.fn();
  const value = "def greet():\n    return 42\n";
  const props = {
    value,
    filename: "sample.py",
    label: "Source",
    onPosition: remember,
  };
  const rendered = render(<SourceEditor {...props} wrap={false} />);
  const editor = EditorView.findFromDOM(screen.getByLabelText("Source"))!;
  act(() => editor.dispatch({ selection: { anchor: 4, head: 9 } }));
  rendered.rerender(<SourceEditor {...props} wrap />);
  expect(EditorView.findFromDOM(screen.getByLabelText("Source"))).toBe(editor);
  expect(editor.state.selection.main.from).toBe(4);
  expect(editor.contentDOM.classList.contains("cm-lineWrapping")).toBe(true);
  rendered.rerender(<SourceEditor {...props} line={2} />);
  expect(editor.state.selection.main.head).toBe(value.indexOf("    return"));
  rendered.unmount();
  expect(remember).toHaveBeenCalledWith(
    expect.objectContaining({ anchor: 13, head: 13 }),
  );
  render(
    <SourceEditor
      {...props}
      position={{ anchor: 4, head: 9, top: 0, left: 0 }}
    />,
  );
  const restored = EditorView.findFromDOM(screen.getByLabelText("Source"))!;
  expect(restored.state.selection.main.from).toBe(4);
  expect(restored.state.selection.main.to).toBe(9);
});
