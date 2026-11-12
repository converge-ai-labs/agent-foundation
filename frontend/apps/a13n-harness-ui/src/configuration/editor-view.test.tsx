// @vitest-environment jsdom
import { afterEach, beforeEach, expect, it, vi } from "vitest";
import {
  act,
  cleanup,
  fireEvent,
  render,
  screen,
} from "@testing-library/react";
import { EditorView } from "codemirror";
import { history, undo, undoDepth } from "@codemirror/commands";
import { Transaction } from "@codemirror/state";

const historyExtension = history();
import { SourceEditor } from "./editor-view";
import { AppearanceProvider } from "../appearance/react";
import { AppearanceStore } from "../appearance/store";

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

it("opens Go to line with the advertised shortcut instead of the default Find next command", () => {
  render(
    <SourceEditor value={"first\nsecond\n"} language="plain" label="Source" />,
  );
  const content = screen.getByRole("textbox", { name: "Source" });
  fireEvent.keyDown(content, { key: "g", ctrlKey: true });
  expect(screen.getByRole("textbox", { name: "Go to line:" })).toBeTruthy();
  expect(screen.queryByRole("textbox", { name: "Find" })).toBeNull();
});

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

it("reports cursor and selection changes without treating selection as an edit", () => {
  const onCursor = vi.fn();
  const onChange = vi.fn();
  render(
    <SourceEditor
      value={"first\nsecond"}
      label="Source"
      onCursor={onCursor}
      onChange={onChange}
    />,
  );
  expect(onCursor).toHaveBeenLastCalledWith({
    line: 1,
    column: 1,
    selected: 0,
  });
  const editor = EditorView.findFromDOM(screen.getByLabelText("Source"))!;
  act(() => editor.dispatch({ selection: { anchor: 1, head: 9 } }));
  expect(onCursor).toHaveBeenLastCalledWith({
    line: 2,
    column: 4,
    selected: 8,
  });
  expect(onChange).not.toHaveBeenCalled();
});

it("updates the accessible label without replacing editor state or history", () => {
  const rendered = render(
    <SourceEditor
      value="hello"
      extensions={historyExtension}
      label="File source"
    />,
  );
  const content = screen.getByRole("textbox", { name: "File source" });
  const view = EditorView.findFromDOM(content)!;
  act(() => {
    view.dispatch({
      changes: { from: 5, insert: " world" },
      annotations: Transaction.userEvent.of("input.type"),
    });
    view.dispatch({ selection: { anchor: 1, head: 4 } });
  });
  expect(undoDepth(view.state)).toBe(1);
  rendered.rerender(
    <SourceEditor
      value="hello"
      extensions={historyExtension}
      label="文件源代码"
    />,
  );
  expect(screen.getByRole("textbox", { name: "文件源代码" })).toBe(content);
  expect(EditorView.findFromDOM(content)).toBe(view);
  expect(view.state.selection.main.anchor).toBe(1);
  expect(view.state.selection.main.head).toBe(4);
  act(() => {
    expect(undo(view)).toBe(true);
  });
  expect(view.state.doc.toString()).toBe("hello");
});

it("remeasures font changes without replacing source state, saving or losing undo", () => {
  const store = new AppearanceStore(window);
  const onChange = vi.fn();
  const onSave = vi.fn();
  render(
    <AppearanceProvider store={store}>
      <SourceEditor
        value="hello"
        label="Source"
        onChange={onChange}
        onSave={onSave}
        extensions={historyExtension}
      />
    </AppearanceProvider>,
  );
  const content = screen.getByRole("textbox", { name: "Source" });
  const view = EditorView.findFromDOM(content)!;
  act(() =>
    view.dispatch({
      changes: { from: 5, insert: " world" },
      selection: { anchor: 1, head: 4 },
      annotations: Transaction.userEvent.of("input.type"),
    }),
  );
  onChange.mockClear();
  const measure = vi.spyOn(view, "requestMeasure");
  act(() => store.setSize("code", 18));
  expect(EditorView.findFromDOM(content)).toBe(view);
  expect(view.state.selection.main.anchor).toBe(1);
  expect(view.state.selection.main.head).toBe(4);
  expect(measure).toHaveBeenCalled();
  expect(onSave).not.toHaveBeenCalled();
  expect(onChange).not.toHaveBeenCalled();
  expect(undoDepth(view.state)).toBe(1);
  act(() => {
    expect(undo(view)).toBe(true);
  });
  expect(view.state.doc.toString()).toBe("hello");
  localStorage.clear();
});

it("keeps Find inside the file editor and refocuses its existing search field", () => {
  const fallback = vi.fn();
  window.addEventListener("keydown", fallback);
  const change = vi.fn();
  try {
    render(
      <SourceEditor
        value="first match\nsecond match\n"
        onChange={change}
        filename="example.txt"
        label="File source"
      />,
    );
    const source = screen.getByRole("textbox", { name: "File source" });
    source.focus();
    expect(fireEvent.keyDown(source, { key: "f", ctrlKey: true })).toBe(false);
    const field = screen.getByRole("textbox", {
      name: "Find",
    }) as HTMLInputElement;
    expect(document.activeElement).toBe(field);
    fireEvent.change(field, { target: { value: "match" } });
    fireEvent.keyDown(field, { key: "f", ctrlKey: true });
    expect(document.activeElement).toBe(field);
    expect(field.value).toBe("match");
    expect(field.selectionStart).toBe(0);
    expect(field.selectionEnd).toBe(5);
    fireEvent.keyDown(field, { key: "Escape" });
    expect(screen.queryByRole("textbox", { name: "Find" })).toBeNull();
    expect(document.activeElement).toBe(source);
    expect(change).not.toHaveBeenCalled();
    expect(fallback).not.toHaveBeenCalled();
  } finally {
    window.removeEventListener("keydown", fallback);
  }
});
