// Real-browser regression: start Vite, then run with agent-browser on PATH:
// node tests/file-editor-smoke.mjs http://127.0.0.1:5174
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";

const session = `file-editor-smoke-${process.pid}`;
const origin = process.argv[2] ?? "http://127.0.0.1:5174";
const browser = (...args) =>
  execFileSync("agent-browser", ["--session", session, ...args], {
    encoding: "utf8",
    timeout: 35_000,
  }).trim();
const evaluate = (code) => JSON.parse(browser("eval", code));
const click = (name) =>
  browser("find", "role", "button", "click", "--name", name, "--exact");
const wait = (code) => browser("wait", "--fn", code);
const view = "fileEditorFixture.view()";
const selection = () => evaluate(`${view}.state.selection.main.toJSON()`);
const move = (point) =>
  browser(
    "mouse",
    "move",
    String(Math.round(point.left)),
    String(Math.round((point.top + point.bottom) / 2)),
  );
try {
  browser("open", `${origin}/tests/file-editor.html`);
  browser("wait", '[aria-label="Markdown preview"]');
  assert.equal(evaluate('document.querySelector(".cm-editor")'), null);
  click("Text");
  browser("wait", ".cm-content");
  wait(`${view}.contentHeight > 100`);
  const original = evaluate(`${view}.state.doc.toString()`);
  // Drag a real pointer on the active line; DOM selection alone misses the
  // regression where the editor's opaque line obscures its selection layer.
  const points = evaluate(
    `(() => { const v = ${view}; const start = v.state.doc.line(3).from; return [v.coordsAtPos(start), v.coordsAtPos(start + 20)]; })()`,
  );
  move(points[0]);
  browser("mouse", "down");
  move(points[1]);
  browser("mouse", "up");
  wait(
    `${view}.state.selection.main.to - ${view}.state.selection.main.from === 20`,
  );
  assert.equal(
    evaluate("window.getSelection().toString()"),
    "Select this sentence",
  );
  evaluate(
    'window.addEventListener("copy", e => { window.copiedText = e.clipboardData.getData("text/plain"); })',
  );
  browser("press", "Control+c");
  assert.equal(evaluate("window.copiedText"), "Select this sentence");
  for (const dark of [false, true]) {
    evaluate(`document.documentElement.classList.toggle("dark", ${dark})`);
    const colors = evaluate(
      `({ selection: getComputedStyle(document.querySelector(".cm-selectionBackground")).backgroundColor, line: getComputedStyle(document.querySelector(".cm-activeLine")).backgroundColor })`,
    );
    assert.match(colors.selection, /\/ 0\.26\)/);
    assert.match(colors.line, /^rgba\(.+, 0\.0/);
  }
  evaluate('document.documentElement.classList.remove("dark")');
  const selected = selection();
  click("Wrap");
  assert.deepEqual(selection(), selected);
  assert.equal(evaluate(`${view}.hasFocus`), true);
  click("Find");
  browser("wait", '[aria-label="Find"]');
  assert.equal(
    evaluate('document.activeElement.getAttribute("aria-label")'),
    "Find",
  );
  browser("press", "Control+a");
  // Search commits on keyup/change; CLI bulk typing uses insertText without
  // keyboard events. Send real keystrokes so Enter searches the current query.
  for (const key of "checklist") browser("press", key);
  browser("press", "Enter");
  assert.equal(
    evaluate(
      `${view}.state.sliceDoc(${view}.state.selection.main.from, ${view}.state.selection.main.to)`,
    ),
    "checklist",
  );
  browser("press", "Escape");
  browser("click", '[aria-label^="Go to line, current"]');
  browser("wait", '.cm-goto-line input[name="line"]');
  browser("fill", '.cm-goto-line input[name="line"]', "3");
  browser("press", "Enter");
  assert.equal(
    evaluate(
      `${view}.state.doc.lineAt(${view}.state.selection.main.head).number`,
    ),
    3,
  );
  browser("keyboard", "inserttext", "Edited: ");
  const edited = evaluate(`${view}.state.doc.toString()`);
  assert.notEqual(edited, original);
  const editedSelection = selection();
  evaluate(
    `window.retainedEditor = ${view}; ${view}.scrollDOM.scrollTop = 320`,
  );
  wait(`${view}.scrollDOM.scrollTop === 320`);
  click("Preview");
  assert.match(
    evaluate('document.querySelector("article").textContent'),
    /Edited: Select/,
  );
  click("Text");
  wait(`${view}.scrollDOM.clientHeight > 0`);
  assert.equal(evaluate(`${view} === window.retainedEditor`), true);
  assert.deepEqual(selection(), editedSelection);
  assert.equal(evaluate(`${view}.scrollDOM.scrollTop`), 320);
  // Focus without clicking into the document (which would move the cursor).
  browser("focus", ".cm-content");
  browser("press", "Control+z");
  assert.equal(evaluate(`${view}.state.doc.toString()`), original);
  browser("press", "Control+Shift+z");
  assert.equal(evaluate(`${view}.state.doc.toString()`), edited);
  browser("press", "Control+s");
  wait(`fileEditorFixture.disk() === ${JSON.stringify(edited)}`);
  assert.equal(evaluate(`${view}.state.doc.toString()`), edited);
  // A requested Markdown source line opens Text directly.
  browser("open", `${origin}/tests/file-editor.html?line=7`);
  browser("wait", ".cm-content");
  assert.equal(
    evaluate(
      `${view}.state.doc.lineAt(${view}.state.selection.main.head).number`,
    ),
    7,
  );
  // Narrow plain-text editing keeps long lines and actions inside the viewport.
  browser("set", "viewport", "390", "844");
  browser("open", `${origin}/tests/file-editor.html?plain=1`);
  browser("wait", ".cm-content");
  assert.equal(evaluate(`${view}.lineWrapping`), true);
  assert.equal(
    evaluate("document.documentElement.scrollWidth <= innerWidth"),
    true,
  );
  click("Find");
  browser("wait", '[aria-label="Find"]');
  assert.equal(
    evaluate("document.documentElement.scrollWidth <= innerWidth"),
    true,
  );
  console.log(
    "PASS: drag selection, copy, themes, wrap, find, go-to-line, Markdown undo/scroll, save, narrow text",
  );
} finally {
  browser("close");
}
