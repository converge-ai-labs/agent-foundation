import { mkdir, mkdtemp, writeFile } from "node:fs/promises";
import { tmpdir } from "node:os";
import { join } from "node:path";
import { codeThemes } from "a13n-ui/code-theme";
import { describe, expect, test } from "vitest";
import { render } from "../blog/markdown";
import { indexPage, postPage } from "../blog/pages";
import { loadPosts } from "../blog/posts";

const FRONT = {
  title: "Sessions that outlive their workers",
  description: "Losing a worker costs seconds.",
  date: "2026-10-14",
  authors: "[Ada]",
};

/** A blog directory holding one post per entry: slug -> front matter overrides and files. */
async function blogDir(
  posts: Record<
    string,
    { front?: object; body?: string; files?: Record<string, string> }
  >,
) {
  const dir = await mkdtemp(join(tmpdir(), "a13n-blog-"));
  for (const [
    slug,
    { front = {}, body = "Text.", files = {} },
  ] of Object.entries(posts)) {
    await mkdir(join(dir, slug));
    const yaml = Object.entries({ ...FRONT, ...front })
      .map(([key, value]) => `${key}: ${value}`)
      .join("\n");
    await writeFile(
      join(dir, slug, "index.md"),
      `---\n${yaml}\n---\n\n${body}\n`,
    );
    for (const [name, content] of Object.entries(files))
      await writeFile(join(dir, slug, name), content);
  }
  return dir;
}

describe("posts", () => {
  test("list newest first with reading time, cover and served files", async () => {
    const dir = await blogDir({
      older: { front: { date: "2026-09-01" } },
      newer: {
        body: "word ".repeat(690),
        files: { "cover.svg": "<svg></svg>", "shot.png": "png" },
      },
    });
    const posts = await loadPosts(dir);
    expect(posts.map((p) => p.slug)).toEqual(["newer", "older"]);
    expect(posts[0]).toMatchObject({
      minutes: 3,
      cover: "<svg></svg>",
      files: ["shot.png"],
    });
    expect(posts[1]).toMatchObject({
      minutes: 1,
      cover: null,
      authors: ["Ada"],
    });
  });

  test.each([
    [{ title: '""' }, '"title" must be a non-empty string'],
    [{ date: "Oct 14" }, '"date" must be a YYYY-MM-DD date'],
    [{ authors: "[]" }, '"authors" must list at least one name'],
  ])("reject front matter %o", async (front, message) => {
    const dir = await blogDir({ post: { front } });
    await expect(loadPosts(dir)).rejects.toThrow(
      `posts/post/index.md: ${message}`,
    );
  });

  test("reject a folder that is not a slug", async () => {
    const dir = await blogDir({ "Draft Post": {} });
    await expect(loadPosts(dir)).rejects.toThrow(
      "folder name must be a lowercase slug",
    );
  });
});

describe("markdown", () => {
  test("code takes the shared palette under its file name or language", async () => {
    const { html } = await render(
      '```python title="worker.py"\nasync def run(): pass\n```\n\n```bash\nmake site-build\n```',
      "",
    );
    expect(html).toContain(
      '<div class="cb-head"><span>worker.py</span><button class="cb-copy" type="button">Copy</button></div>',
    );
    expect(html).toContain("<span>Terminal</span>");
    const keyword = codeThemes.light.tokenColors.find(
      (rule) => "scope" in rule && rule.scope.includes("keyword"),
    )!.settings.foreground;
    expect(html.toLowerCase()).toContain(`color:${keyword}">async`);
    expect(html).not.toMatch(/background-color/);
  });

  test("headings get GitHub's IDs and a link to themselves", async () => {
    const { html } = await render(
      "## One heartbeat, per worker\n\n## One heartbeat, per worker",
      "",
    );
    expect(html).toContain(
      '<h2 id="one-heartbeat-per-worker">One heartbeat, per worker<a class="anchor" href="#one-heartbeat-per-worker" aria-label="Section link">#</a></h2>',
    );
    expect(html).toContain('id="one-heartbeat-per-worker-1"');
  });

  test("a lone SVG image becomes an inline figure with its caption", async () => {
    const dir = await mkdtemp(join(tmpdir(), "a13n-figure-"));
    await writeFile(
      join(dir, "f.svg"),
      '<?xml version="1.0"?>\n<svg viewBox="0 0 760 270"><line class="pa"/></svg>',
    );
    const { html, words } = await render(
      '![A timeline](f.svg "Figure 1. Worker A stops.")\n\nInline ![shot](s.png).',
      dir,
    );
    expect(html).toContain(
      '<figure class="fig"><div class="fig-scroll" role="img" aria-label="A timeline" style="--w: 760px"><svg viewBox="0 0 760 270"><line class="pa"/></svg></div><figcaption><b>Figure 1.</b> Worker A stops.</figcaption></figure>',
    );
    expect(html).toContain('<img src="s.png" alt="shot" vite-ignore="">');
    expect(words).toBe(1);
  });
});

describe("pages", () => {
  test("each page has its canonical URL and escaped structured data", async () => {
    const dir = await blogDir({
      post: { front: { title: '"A </script> <b>title</b>"' } },
    });
    const posts = await loadPosts(dir);
    const page = postPage(posts[0], posts);
    expect(page.match(/rel="canonical"/g)).toHaveLength(1);
    expect(page).toContain(
      '<link rel="canonical" href="https://a13n.dev/blog/post/" />',
    );
    expect(page).toContain(
      "<title>A &#60;/script&#62; &#60;b&#62;title&#60;/b&#62; — a13n</title>",
    );
    const [, data] =
      /<script type="application\/ld\+json">(.+?)<\/script>/.exec(page)!;
    expect(JSON.parse(data)).toMatchObject({
      "@type": "BlogPosting",
      headline: "A </script> <b>title</b>",
      datePublished: "2026-10-14",
    });
    expect(indexPage(posts)).toContain(
      '<link rel="canonical" href="https://a13n.dev/blog/" />',
    );
  });
});
