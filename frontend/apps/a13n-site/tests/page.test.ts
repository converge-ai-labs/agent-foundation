import { readdirSync, readFileSync } from "node:fs";
import { join } from "node:path";
import { expect, test } from "vitest";
import html from "../index.html?raw";

test("the heading and structured data name the site a13n", () => {
  // The hero draws its glyphs with CSS, so this is the heading's only text.
  expect(html).toMatch(/<h1[^>]*>\s*<span class="vh">a13n<\/span>/);
  const [, data] =
    html.match(/<script type="application\/ld\+json">([^<]+)<\/script>/) ?? [];
  const graph: { name: string }[] = JSON.parse(data ?? "")["@graph"];
  expect(graph.map((node) => node.name)).toEqual(["a13n", "a13n"]);
  expect(html).toContain('<meta property="og:site_name" content="a13n" />');
  expect(html).toContain('<meta name="application-name" content="a13n" />');
});

test("each page imports one stylesheet, so a build keeps shared rules before its own", () => {
  // The build links CSS shared between pages after a page's own CSS.
  const src = join(import.meta.dirname, "../src");
  for (const name of readdirSync(src).filter((n) => n.endsWith(".ts"))) {
    const source = readFileSync(join(src, name), "utf8");
    const sheets = source.match(/^import "[^"]+";$/gm) ?? [];
    expect(sheets.length, name).toBeLessThanOrEqual(1);
  }
});
