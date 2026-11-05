import assert from "node:assert/strict";
import { test } from "node:test";
import { fromMarkdown } from "mdast-util-from-markdown";
import { visit } from "unist-util-visit";
import { remarkExplicitAnchors } from "../lib/remark-explicit-anchors.ts";

test("empty span anchors survive alone, inline, and adjacent on one line", () => {
  const tree = fromMarkdown(
    '<span id="standalone"></span>\n\nBefore <span id="inline"></span> after.\n\n<span id="first"></span><span id=\'second\'></span>\n',
  );
  remarkExplicitAnchors()(tree);
  const ids: string[] = [];
  visit(tree, "mdxJsxTextElement", (node) => {
    assert.equal(node.name, "span");
    assert.deepEqual(node.children, []);
    assert.equal(node.attributes.length, 1);
    const id = node.attributes[0];
    assert.equal(id.type, "mdxJsxAttribute");
    if (id.type !== "mdxJsxAttribute") throw new Error("Expected literal id");
    assert.equal(id.name, "id");
    assert.equal(typeof id.value, "string");
    ids.push(id.value as string);
  });
  assert.deepEqual(ids, ["standalone", "inline", "first", "second"]);
  const texts: string[] = [];
  visit(tree, "text", (node) => {
    texts.push(node.value);
  });
  assert.deepEqual(texts, ["Before ", " after."]);
});

test("anchors do not enable raw HTML, nonempty spans, or code examples", () => {
  const markdown = [
    '<span id="event" onclick="alert(1)"></span>',
    '<span id="content">text</span>',
    '<span id="expression">{value}</span>',
    '<span id="bad id"></span>',
    '<div id="other"></div>',
    "<script>alert(1)</script>",
    '`<span id="code"></span>`',
    '```html\n<span id="fenced"></span>\n```',
  ].join("\n\n");
  const tree = fromMarkdown(markdown);
  const before = structuredClone(tree);
  remarkExplicitAnchors()(tree);
  assert.deepEqual(tree, before);
});
