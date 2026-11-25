import assert from "node:assert/strict";
import { readFile } from "node:fs/promises";
import { test } from "node:test";
import { JSDOM } from "jsdom";

const dist = new URL("../dist/", import.meta.url);

async function builtPage(path) {
  const html = await readFile(new URL(path, dist), "utf8");
  const { window } = new JSDOM(html);
  for (const link of window.document.querySelectorAll(
    'link[rel~="stylesheet"]',
  )) {
    const style = window.document.createElement("style");
    const asset = link.getAttribute("href").replace(/^\//, "");
    style.textContent = await readFile(new URL(asset, dist), "utf8");
    link.replaceWith(style);
  }
  return window;
}

test("built blog and not-found pages retain their styles over the shared defaults", async () => {
  const blog = await builtPage("blog/index.html");
  const notFound = await builtPage("404.html");
  try {
    const textFamily = (page) =>
      page
        .getComputedStyle(page.document.documentElement)
        .getPropertyValue("--text")
        .trim();
    const blogFamily = textFamily(blog);
    const sharedFamily = textFamily(notFound);
    assert.ok(blogFamily && sharedFamily);
    assert.notEqual(blogFamily, sharedFamily);
    const main = notFound.getComputedStyle(
      notFound.document.querySelector("main"),
    );
    assert.ok(parseFloat(main.paddingTop) > 0);
    assert.ok(parseFloat(main.paddingBottom) > 0);
  } finally {
    blog.close();
    notFound.close();
  }
});
