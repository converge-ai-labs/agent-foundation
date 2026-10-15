import assert from "node:assert/strict";
import { createReadStream } from "node:fs";
import { glob, readFile, stat, writeFile } from "node:fs/promises";
import { Readable } from "node:stream";
import { fileURLToPath } from "node:url";
import { parse } from "parse5";
import { parseSitemap, SitemapStream, streamToPromise } from "sitemap";

const args = process.argv.slice(2);
assert.ok(
  args.length === 0 || (args.length === 1 && args[0] === "--check"),
  "Usage: node scripts/sitemap.mjs [--check]",
);
const output = new URL("../dist/", import.meta.url);
const origin = "https://a13n.dev";
const urls = new Set();

// The assembled HTML owns indexability and canonical URLs for both applications.
for await (const path of glob("**/*.html", { cwd: fileURLToPath(output) })) {
  const document = parse(await readFile(new URL(path, output), "utf8"));
  const head = document.childNodes
    .find((node) => node.tagName === "html")
    ?.childNodes.find((node) => node.tagName === "head");
  assert.ok(head, `${path} has no head`);
  const tags = head.childNodes
    .filter((node) => node.tagName === "meta" || node.tagName === "link")
    .map((node) => ({
      tag: node.tagName,
      ...Object.fromEntries(node.attrs.map(({ name, value }) => [name, value])),
    }));
  if (
    tags.some(
      (tag) =>
        tag.tag === "meta" &&
        ((/^(robots|googlebot)$/i.test(tag.name ?? "") &&
          /\b(noindex|none)\b/i.test(tag.content ?? "")) ||
          /^refresh$/i.test(tag["http-equiv"] ?? "")),
    )
  )
    continue;

  const canonicals = tags.filter(
    (tag) => tag.tag === "link" && /\bcanonical\b/i.test(tag.rel ?? ""),
  );
  assert.equal(canonicals.length, 1, `${path} must have one canonical URL`);
  const canonical = canonicals[0].href;
  const publishedPath = path
    .replace(/(^|\/)index\.html$/, "$1")
    .replace(/\.html$/, "");
  assert.equal(canonical, `${origin}/${publishedPath}`, path);
  assert.ok(!urls.has(canonical), `Duplicate canonical URL: ${canonical}`);
  urls.add(canonical);
}
assert.ok(urls.has(`${origin}/`), "The home page must be indexed");
assert.ok(urls.has(`${origin}/docs/`), "Assemble the documentation first");
assert.ok(urls.size <= 50_000, "The sitemap exceeds the 50,000 URL limit");

// Preserve the published docs endpoint and catch drift from its source inventory.
const docs = await parseSitemap(
  createReadStream(new URL("docs/sitemap.xml", output)),
);
const docsUrls = new Set(docs.map((page) => page.url));
assert.equal(docs.length, docsUrls.size, "Duplicate documentation sitemap URL");
assert.deepEqual(
  docsUrls,
  new Set([...urls].filter((url) => url.startsWith(`${origin}/docs/`))),
  "The documentation sitemap must match its indexable pages",
);

const robots = await readFile(new URL("robots.txt", output), "utf8");
assert.match(robots, /^User-agent: \*$/m);
assert.match(robots, /^Allow: \/$/m);
assert.deepEqual(robots.match(/^Sitemap: .+$/gm), [
  `Sitemap: ${origin}/sitemap.xml`,
]);

const sitemap = new URL("sitemap.xml", output);
if (!args.includes("--check")) {
  const xml = await streamToPromise(
    Readable.from([...urls].sort().map((url) => ({ url }))).pipe(
      new SitemapStream({
        xmlns: { news: false, xhtml: false, image: false, video: false },
      }),
    ),
  );
  await writeFile(sitemap, xml);
}

assert.ok(
  (await stat(sitemap)).size <= 50 * 1024 * 1024,
  "The sitemap exceeds the 50 MB limit",
);
const pages = await parseSitemap(createReadStream(sitemap));
const sitemapUrls = new Set(pages.map((page) => page.url));
assert.equal(pages.length, sitemapUrls.size, "Duplicate sitemap URL");
assert.deepEqual(
  sitemapUrls,
  urls,
  "The sitemap must cover every indexable page",
);
console.log(`Checked /sitemap.xml covering ${urls.size} canonical pages`);
