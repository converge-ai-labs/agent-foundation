// Optional real-browser regression: start Vite, then run with agent-browser on PATH:
// node tests/browser-annotation-smoke.mjs http://127.0.0.1:5174
import assert from "node:assert/strict";
import { execFileSync } from "node:child_process";

const session = `annotation-smoke-${process.pid}`;
const origin = process.argv[2] ?? "http://127.0.0.1:5174";
const browser = (...args) =>
  execFileSync("agent-browser", ["--session", session, ...args], {
    encoding: "utf8",
    timeout: 30_000,
  }).trim();
const evaluate = (code) => JSON.parse(browser("eval", code));
const click = (name) =>
  browser("find", "role", "button", "click", "--name", name);
const move = (x, y) =>
  browser("mouse", "move", String(Math.round(x)), String(Math.round(y)));
try {
  browser("open", `${origin}/tests/browser-annotation.html`);
  browser(
    "wait",
    "--fn",
    'document.activeElement?.getAttribute("aria-label") === "Copy PNG (Ctrl/⌘ Enter)"',
  );
  click("Text (T)");
  browser("click", "canvas");
  // Do not use fill/type: they may focus the textarea and hide the regression.
  for (const key of "Hello") browser("press", key);
  assert.deepEqual(
    evaluate(
      '({tag: document.activeElement.tagName, value: document.querySelector("textarea").value})',
    ),
    {
      tag: "TEXTAREA",
      value: "Hello",
    },
  );
  click("Close annotation");
  browser("wait", "--text", "Discard annotations?");
  browser("press", "Escape");
  assert.equal(evaluate('document.querySelector("textarea").value'), "Hello");
  click("Apply text (Ctrl/⌘ Enter)");
  browser("press", "Escape");
  browser("wait", "--text", "Discard annotations?");
  click("Keep editing");
  click("Undo (Ctrl/⌘ Z)");
  click("Redo (Ctrl/⌘ Shift Z)");
  assert.equal(
    evaluate('annotationFixture.stage().findOne("Text").text()'),
    "Hello",
  );

  click("Rectangle (R)");
  const rect = evaluate(
    'document.querySelector("canvas").getBoundingClientRect().toJSON()',
  );
  move(rect.x + 40, rect.y + 60);
  browser("mouse", "down");
  move(rect.x + 140, rect.y + 160);
  browser("mouse", "up");
  const id = evaluate('annotationFixture.stage().findOne("Rect").id()');
  browser("select", '[aria-label="Select annotation"]', id);
  const handle = evaluate(
    'annotationFixture.stage().findOne(".middle-right").getAbsolutePosition()',
  );
  move(rect.x + handle.x, rect.y + handle.y);
  browser("mouse", "down");
  move(rect.x + handle.x + 50, rect.y + handle.y);
  browser("mouse", "up");
  const resized = evaluate(
    '(() => {const n = annotationFixture.stage().findOne("Rect"); return {width: n.width(), scaleX: n.scaleX(), scaleY: n.scaleY(), stroke: n.strokeWidth()}})()',
  );
  assert.equal(resized.scaleX, 1);
  assert.equal(resized.scaleY, 1);
  assert.equal(resized.stroke, 4);
  click("Undo (Ctrl/⌘ Z)");
  assert.ok(
    evaluate('annotationFixture.stage().findOne("Rect").width()') <
      resized.width,
  );
  click("Redo (Ctrl/⌘ Shift Z)");
  assert.equal(
    evaluate('annotationFixture.stage().findOne("Rect").width()'),
    resized.width,
  );

  // Test exported pixels, not only Konva attributes, at two preview zoom levels.
  for (const zoom of ["0.5", "1"]) {
    browser("select", '[aria-label="Preview zoom"]', zoom);
    const pixels = evaluate(`(async () => {
      const n = annotationFixture.stage().findOne("Rect");
      const result = await annotationFixture.export({x: 0, y: 0, width: 1600, height: 800});
      const image = new Image(); image.src = result.png; await image.decode();
      const canvas = document.createElement("canvas"); canvas.width = 1600; canvas.height = 800;
      const ctx = canvas.getContext("2d"); ctx.drawImage(image, 0, 0);
      const red = (x,y) => { const p = ctx.getImageData(x,y,1,1).data; return p[0] > 230 && p[1] < 90 && p[2] < 90; };
      const cx = Math.round(n.x() + n.width()/2), cy = Math.round(n.y() + n.height()/2);
      let horizontal = 0, vertical = 0;
      for (let d = -10; d <= 10; d++) {
        horizontal += red(cx, Math.round(n.y()) + d);
        vertical += red(Math.round(n.x()) + d, cy);
      }
      return {width: result.width, height: result.height, horizontal, vertical};
    })()`);
    assert.equal(pixels.width, 1600);
    assert.equal(pixels.height, 800);
    // Subpixel positions leave 3–4 solid pixels inside a 4px antialiased stroke.
    // A 1.5x horizontal transform must not produce a 5–6px vertical edge.
    for (const edge of [pixels.horizontal, pixels.vertical])
      assert.ok(edge >= 3 && edge <= 4, JSON.stringify(pixels));
  }
  click("Close annotation");
  click("Discard");
  browser("wait", "--fn", '!document.querySelector("[role=dialog]")');
  console.log(
    "PASS: native text focus, discard/keep, resize/history and source-pixel strokes at 50%/100% zoom",
  );
} finally {
  browser("close");
}
