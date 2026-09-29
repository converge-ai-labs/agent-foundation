// Check every internal link and anchor in the exported site; fail the build on any broken one.
import { existsSync, readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

const out = new URL("../out/", import.meta.url).pathname;

function* htmlFiles(directory) {
  for (const entry of readdirSync(directory, { withFileTypes: true })) {
    const path = join(directory, entry.name);
    if (entry.isDirectory() && entry.name !== "_next") yield* htmlFiles(path);
    else if (entry.name.endsWith(".html")) yield path;
  }
}

const decode = (value) =>
  value
    .replaceAll("&amp;", "&")
    .replaceAll("&quot;", '"')
    .replaceAll("&#x27;", "'");
const pages = new Map();
for (const file of htmlFiles(out)) {
  const html = readFileSync(file, "utf8");
  pages.set(file, {
    ids: new Set(
      [...html.matchAll(/\sid="([^"]+)"/g)].map((match) => decode(match[1])),
    ),
    links: [...html.matchAll(/<a\s[^>]*?href="([^"]*)"/g)].map((match) =>
      decode(match[1]),
    ),
  });
}

function resolveTarget(file, path) {
  const base = path.startsWith("/") ? out : join(file, "..");
  let target = join(base, decodeURIComponent(path));
  if (existsSync(target) && statSync(target).isDirectory())
    target = join(target, "index.html");
  return target;
}

const errors = [];
for (const [file, page] of pages) {
  for (const link of page.links) {
    if (/^[a-z][a-z0-9+.-]*:/i.test(link) || link.startsWith("//")) continue;
    const [path, fragment] = link.split("#", 2);
    const target = path ? resolveTarget(file, path) : file;
    const source = relative(out, file);
    if (!existsSync(target)) errors.push(`${source} -> ${link}: missing page`);
    else if (
      fragment &&
      pages.has(target) &&
      !pages.get(target).ids.has(decodeURIComponent(fragment))
    ) {
      errors.push(`${source} -> ${link}: missing anchor`);
    }
  }
}

if (errors.length > 0) {
  console.error([...new Set(errors)].sort().join("\n"));
  console.error(`\n${errors.length} broken links`);
  process.exit(1);
}
console.log(`Checked links in ${pages.size} pages`);
