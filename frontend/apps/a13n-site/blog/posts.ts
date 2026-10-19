import { readdir, readFile } from "node:fs/promises";
import { join } from "node:path";
import { parse } from "yaml";
import { render } from "./markdown.ts";

/** The repository's blog/ directory: one folder per post, named by its slug. */
export const POSTS_DIR = join(import.meta.dirname, "../../../../blog");

export interface Post {
  slug: string;
  title: string;
  description: string;
  /** ISO date, YYYY-MM-DD */
  date: string;
  authors: string[];
  minutes: number;
  html: string;
  /** The cover drawing's SVG markup, from cover.svg beside the post. */
  cover: string | null;
  /** Files beside the post that its pages serve, such as images. */
  files: string[];
}

const SLUG = /^[a-z0-9]+(-[a-z0-9]+)*$/;
const DATE = /^\d{4}-\d{2}-\d{2}$/;
const FRONT_MATTER = /^---\n([\s\S]*?)\n---\n/;

/** Every post, newest first. */
export async function loadPosts(dir = POSTS_DIR): Promise<Post[]> {
  const entries = await readdir(dir, { withFileTypes: true });
  const posts = await Promise.all(
    entries
      .filter((entry) => entry.isDirectory())
      .map((entry) => loadPost(join(dir, entry.name), entry.name)),
  );
  return posts.sort(
    (a, b) => b.date.localeCompare(a.date) || a.slug.localeCompare(b.slug),
  );
}

async function loadPost(dir: string, slug: string): Promise<Post> {
  const source = `blog/${slug}/index.md`;
  if (!SLUG.test(slug))
    throw new Error(`${source}: the folder name must be a lowercase slug`);
  const text = await readFile(join(dir, "index.md"), "utf8");
  const match = FRONT_MATTER.exec(text);
  if (!match) throw new Error(`${source}: missing front matter`);
  const meta = frontMatter(parse(match[1]), source);
  const { html, words } = await render(text.slice(match[0].length), dir);
  const names = await readdir(dir);
  return {
    slug,
    ...meta,
    // 230 words a minute, the usual pace for technical prose
    minutes: Math.max(1, Math.round(words / 230)),
    html,
    cover: names.includes("cover.svg")
      ? await readFile(join(dir, "cover.svg"), "utf8")
      : null,
    files: names.filter((name) => name !== "index.md" && name !== "cover.svg"),
  };
}

function frontMatter(data: unknown, source: string) {
  const meta = (data ?? {}) as Record<string, unknown>;
  const text = (key: string) => {
    const value = meta[key];
    if (typeof value !== "string" || !value.trim())
      throw new Error(`${source}: "${key}" must be a non-empty string`);
    return value.trim();
  };
  const date = text("date");
  if (!DATE.test(date) || Number.isNaN(Date.parse(date)))
    throw new Error(`${source}: "date" must be a YYYY-MM-DD date`);
  const authors = meta.authors;
  if (
    !Array.isArray(authors) ||
    !authors.length ||
    !authors.every((name) => typeof name === "string" && name.trim())
  )
    throw new Error(`${source}: "authors" must list at least one name`);
  return {
    title: text("title"),
    description: text("description"),
    date,
    authors: authors.map((name: string) => name.trim()),
  };
}
