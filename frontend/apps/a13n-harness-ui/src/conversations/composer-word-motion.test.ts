// @vitest-environment jsdom
import { afterEach, expect, it, vi } from "vitest";

vi.hoisted(() => {
  Object.defineProperty(navigator, "platform", {
    value: "MacIntel",
    configurable: true,
  });
});

import { EditorSelection, EditorState } from "@codemirror/state";
import { EditorView, keymap } from "@codemirror/view";
import { defaultKeymap } from "@codemirror/commands";
import { composerWordKeymap } from "./composer-word-motion";

let view: EditorView;
afterEach(() => view?.destroy());

function editor(text: string, anchor = 0) {
  view = new EditorView({
    parent: document.body,
    doc: text,
    selection: { anchor },
    extensions: [
      EditorState.allowMultipleSelections.of(true),
      keymap.of([...composerWordKeymap, ...defaultKeymap]),
    ],
  });
  return view;
}
function move(right: boolean, shiftKey = false) {
  view.contentDOM.dispatchEvent(
    new KeyboardEvent("keydown", {
      key: right ? "ArrowRight" : "ArrowLeft",
      code: right ? "ArrowRight" : "ArrowLeft",
      altKey: true,
      shiftKey,
      bubbles: true,
      cancelable: true,
    }),
  );
  return view.state.selection.main.head;
}

it.each<[string, number[]]>([
  ["我们今天讨论中文输入体验", [2, 4, 6, 8, 10, 12]],
  ["hello中文world", [5, 7, 12]],
  ["你好，世界！", [2, 3, 5, 6]],
  ["中文  输入", [2, 6]],
  ["中文\n输入", [2, 5]],
  ["hello_world next", [11, 16]],
  ["中文👩‍💻输入", [2, 7, 9]],
])("moves Option+Right through %j without editing text", (text, stops) => {
  editor(text);
  for (const stop of stops) expect(move(true)).toBe(stop);
  expect(move(true)).toBe(text.length);
  expect(view.state.doc.toString()).toBe(text);
});

it("moves left by Chinese words, including from word interiors", () => {
  editor("我们今天讨论中文输入体验", 12);
  for (const stop of [10, 8, 6, 4, 2, 0, 0]) expect(move(false)).toBe(stop);
  view.dispatch({ selection: { anchor: 3 } });
  expect(move(false)).toBe(2);
  view.dispatch({ selection: { anchor: 3 } });
  expect(move(true)).toBe(4);
});

it("extends and reverses selections with Shift, and otherwise collapses them", () => {
  editor("我们今天讨论", 2);
  expect(move(true, true)).toBe(4);
  expect(move(true, true)).toBe(6);
  expect(view.state.selection.main.anchor).toBe(2);
  expect(move(false, true)).toBe(4);
  expect(move(false)).toBe(2);
  expect(view.state.selection.main.empty).toBe(true);
  move(true, true);
  expect(move(true)).toBe(4);
  expect(view.state.selection.main.empty).toBe(true);
});

it("leaves composition input to the IME", () => {
  editor("我们今天讨论");
  view.contentDOM.dispatchEvent(
    new CompositionEvent("compositionstart", { bubbles: true }),
  );
  expect(composerWordKeymap[1].run!(view)).toBe(false);
  expect(view.state.selection.main.head).toBe(0);
  view.contentDOM.dispatchEvent(
    new CompositionEvent("compositionend", { bubbles: true }),
  );
});

it("preserves multiple cursors and the main selection", () => {
  editor("我们今天讨论");
  view.dispatch({
    selection: EditorSelection.create(
      [EditorSelection.cursor(0), EditorSelection.cursor(4)],
      1,
    ),
  });
  move(true);
  expect(view.state.selection.ranges.map((range) => range.head)).toEqual([
    2, 6,
  ]);
  expect(view.state.selection.mainIndex).toBe(1);
});
