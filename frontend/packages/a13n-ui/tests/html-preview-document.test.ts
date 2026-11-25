import { expect, it } from "vitest";
import { buildHtmlPreviewDocument } from "../src/html-preview-document";

it("wraps snippets in a themed document with a CSP before author styles", () => {
  const result = buildHtmlPreviewDocument(
    "<style>p { color: tomato }</style><p>Hello</p>",
  );
  expect(result).toMatch(
    /^<!DOCTYPE html><html><head><meta http-equiv="Content-Security-Policy"/,
  );
  expect(result).toContain("default-src 'none'");
  expect(result).toContain("style-src 'unsafe-inline'; img-src data:");
  expect(result).toContain("color-scheme: light dark");
  expect(result).toContain("system-ui, sans-serif");
  expect(result).toContain("<p>Hello</p>");
});

it("preserves document styles, static SVG and native disclosures", () => {
  const result =
    buildHtmlPreviewDocument(`<!doctype html><html lang="en"><head><style>body { padding: 0 }</style></head>
<body class="example"><details open><summary>More</summary><svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="10" /></svg></details></body></html>`);
  expect(result).toContain('<html lang="en"><head><meta');
  expect(result).toContain("<style>body { padding: 0 }</style>");
  expect(result).not.toContain("system-ui");
  expect(result).toContain(
    '<body class="example"><details open=""><summary>More</summary>',
  );
  expect(result).toContain(
    '<svg viewBox="0 0 20 20"><circle cx="10" cy="10" r="10"></circle></svg>',
  );
});

it("drops active markup and external document links while keeping allowed data images and local SVG references", () => {
  const result =
    buildHtmlPreviewDocument(`<!doctype html><head><link rel="preconnect" href="https://example.test">
<meta http-equiv="refresh" content="60"><base href="https://example.test"></head>
<body onload="void 0"><script>void 0</script><iframe title="Nested"></iframe><template><p>Hidden</p></template>
<a href="https://example.test" ping="https://example.test">External link</a>
<img src="https://example.test/image" srcset="https://example.test/image 2x" alt="Remote">
<img src="data:image/png;base64,AAAA" alt="Embedded">
<svg><use href="#mark"/><use href="https://example.test/icon.svg#mark"/></svg></body>`);
  expect(result).not.toMatch(
    /example\.test|http-equiv="refresh"|<base|<script|<iframe|<template|onload|srcset|ping=/,
  );
  expect(result).toContain("<a>External link</a>");
  expect(result).toContain('<img alt="Remote">');
  expect(result).toContain(
    '<img src="data:image/png;base64,AAAA" alt="Embedded">',
  );
  expect(result).toContain('<svg><use href="#mark"></use><use></use></svg>');
});

it("bounds nesting without losing following content", () => {
  const result = buildHtmlPreviewDocument(
    `${"<div>".repeat(500)}middle${"</div>".repeat(500)}<p>After</p>`,
  );
  expect(result).not.toContain("middle");
  expect(result).toContain("<p>After</p>");
});

it("keeps declarative label associations, hidden content and SVG presentation", () => {
  const result =
    buildHtmlPreviewDocument(`<input type="checkbox" id="choice"><label for="choice">Choice</label>
<p hidden>Hidden</p><meter min="0" max="10" low="2" high="8" optimum="5"></meter>
<svg><text font-size="12" dominant-baseline="middle">Label</text></svg>`);
  expect(result).toContain('<label for="choice">Choice</label>');
  expect(result).toContain('<p hidden="">Hidden</p>');
  expect(result).toContain('low="2" high="8" optimum="5"');
  expect(result).toContain(
    '<text font-size="12" dominant-baseline="middle">Label</text>',
  );
});
