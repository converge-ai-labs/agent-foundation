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
