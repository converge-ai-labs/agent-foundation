import assert from "node:assert/strict";
import { readFile, readdir } from "node:fs/promises";
import { fileURLToPath } from "node:url";
import { test } from "node:test";
import { staticClient } from "fumadocs-core/search/client/orama-static";

const output = new URL("../out/", import.meta.url);
const docs = new URL("../../../../docs/", import.meta.url);
async function files(folder, prefix = "") {
  const entries = await readdir(folder, { withFileTypes: true });
  const result = [];
  for (const entry of entries) {
    const path = `${prefix}${entry.name}`;
    if (entry.isDirectory())
      result.push(
        ...(await files(new URL(`${entry.name}/`, folder), `${path}/`)),
      );
    else result.push(path);
  }
  return result;
}
const canonicalPages = (await files(docs)).filter(
  (path) => /\.mdx?$/.test(path) && !path.includes(".zh-CN."),
);

test("every human document has a Chinese static page with matching section anchors", async () => {
  for (const path of canonicalPages) {
    const url = path.replace(/\.mdx?$/, "").replace(/(?:^|\/)index$/, "");
    const pagePath = url
      ? `${url.replace(/\/$/, "")}/index.html`
      : "index.html";
    const english = await readFile(new URL(pagePath, output), "utf8");
    const chinese = await readFile(
      new URL(`zh-CN/${pagePath}`, output),
      "utf8",
    );
    assert.match(english, /<html lang="en"/);
    assert.match(chinese, /<html lang="zh-CN"/);
    const headings = (html) =>
      [...html.matchAll(/<h[2-6]\s[^>]*id="([^"]+)"/g)].map(
        (match) => match[1],
      );
    assert.deepEqual(headings(chinese), headings(english), path);
    assert.match(chinese, /rel="alternate" hrefLang="en"/);
    assert.match(chinese, /rel="alternate" hrefLang="zh-CN"/);
    assert.match(
      chinese,
      /<link rel="canonical" href="https:\/\/a13n\.converge\.ai\/docs\/zh-CN\//,
    );
  }
});

test("machine-readable resources keep canonical English paths and untouched schemas", async () => {
  assert.equal(
    (await files(new URL("md/", output))).filter((path) => path.endsWith(".md"))
      .length,
    canonicalPages.length,
  );
  assert.ok(
    !(await files(new URL("md/", output))).some((path) =>
      path.includes("zh-CN"),
    ),
  );
  for (const path of ["llms.txt", "llms-full.txt"])
    assert.ok(
      !(await readFile(new URL(path, output), "utf8")).includes("/zh-CN/"),
      path,
    );
  const contract = new URL(
    "../../../../proto/a13n-service/openapi.json",
    import.meta.url,
  );
  assert.deepEqual(
    await readFile(new URL("reference/service-openapi.json", output)),
    await readFile(contract),
  );
});

test("static search returns Chinese and technical queries within the selected language", async () => {
  const data = JSON.parse(
    await readFile(new URL("api/search", output), "utf8"),
  );
  const originalFetch = globalThis.fetch;
  globalThis.fetch = async () => Response.json(data);
  try {
    for (const [locale, queries] of [
      ["zh-CN", ["工作空间", "环境", "agent", "MCP", "API key"]],
      ["en", ["workspace", "environment", "MCP"]],
    ]) {
      const client = staticClient({ from: fileURLToPath(output), locale });
      for (const query of queries) {
        const results = await client.search(query);
        assert.ok(results.length > 0, `${locale}: ${query}`);
        assert.ok(
          results.every(
            (result) =>
              result.url.startsWith("/zh-CN/") === (locale === "zh-CN"),
          ),
          `${locale}: ${query} leaked another language`,
        );
      }
    }
  } finally {
    globalThis.fetch = originalFetch;
  }
});
