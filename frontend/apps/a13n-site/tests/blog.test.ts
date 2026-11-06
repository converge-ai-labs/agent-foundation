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

const REPORT = {
  title: "Original evaluation",
  url: "https://example.com/report",
  author: {
    name: "Researcher & collaborator",
    url: "https://example.com/researcher",
    avatar: "https://example.com/avatar.png?size=80&v=1",
  },
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

  test.each([
    [" ", '"authors[0]" must be a non-empty string'],
    [null, '"authors[0].name" must be a non-empty string'],
    [
      { ...REPORT.author, name: "" },
      '"authors[0].name" must be a non-empty string',
    ],
    [
      { ...REPORT.author, url: "/profile" },
      '"authors[0].url" must be an absolute URL',
    ],
    [
      { ...REPORT.author, url: "javascript:alert(1)" },
      '"authors[0].url" must use HTTPS',
    ],
    [
      { ...REPORT.author, avatar: "http://example.com/a.png" },
      '"authors[0].avatar" must use HTTPS',
    ],
    [
      { name: "Ada", url: "https://example.com/ada" },
      '"authors[0].avatar" must be a non-empty string',
    ],
  ])("reject incomplete or unsafe author profiles", async (author, message) => {
    const dir = await blogDir({
      post: { front: { authors: JSON.stringify([author]) } },
    });
    await expect(loadPosts(dir)).rejects.toThrow(message);
  });

  test("reject a folder that is not a slug", async () => {
    const dir = await blogDir({ "Draft Post": {} });
    await expect(loadPosts(dir)).rejects.toThrow(
      "folder name must be a lowercase slug",
    );
  });

  test.each([
    [{ ...REPORT, title: "" }, '"report.title" must be a non-empty string'],
    [{ ...REPORT, url: "/report" }, '"report.url" must be an absolute URL'],
    [
      {
        ...REPORT,
        author: { ...REPORT.author, avatar: "javascript:alert(1)" },
      },
      '"report.author.avatar" must use HTTPS',
    ],
  ])(
    "reject incomplete or unsafe source attribution",
    async (report, message) => {
      const dir = await blogDir({
        post: { front: { report: JSON.stringify(report) } },
      });
      await expect(loadPosts(dir)).rejects.toThrow(message);
    },
  );
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

  test("navigation uses plain heading text and the rendered IDs, excluding code and notes", async () => {
    const { html, headings } = await render(
      "## Work & **state**\n\n### A `run`\n\n## Work & **state**\n\n```text\n## Not a heading\n```\n\nA note.[^1]\n\n[^1]: Supporting text.",
      "",
    );
    expect(headings).toEqual([
      { id: "work--state", title: "Work & state", depth: 2 },
      { id: "a-run", title: "A run", depth: 3 },
      { id: "work--state-1", title: "Work & state", depth: 2 },
    ]);
    for (const heading of headings)
      expect(html).toContain(`id="${heading.id}"`);
    expect(html).toContain('id="footnote-label"');
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

  test("supporting Markdown stays inside a native disclosure with a scrollable table", async () => {
    const { html } = await render(
      "<details>\n<summary>Conditions</summary>\n\n| Task | Outcome |\n| --- | --- |\n| First | **Passed** |\n\n</details>",
      "",
    );
    expect(html).toMatch(
      /<details>\s*<summary>Conditions<\/summary>\s*<div class="table-scroll"><table>[\s\S]*<strong>Passed<\/strong>[\s\S]*<\/table><\/div>\s*<\/details>/,
    );
  });
});

describe("pages", () => {
  test("section navigation nests subsections and safely links to repeated and Unicode headings", async () => {
    const dir = await blogDir({
      post: {
        body: "### First\n\n## Work & state\n\n### Résumé\n\n## Work & state",
      },
    });
    const posts = await loadPosts(dir);
    const page = postPage(posts[0], posts);
    expect(page.match(/aria-label="On this page"/g)).toHaveLength(2);
    expect(page.match(/class="toc-list"/g)).toHaveLength(2);
    expect(page).toContain(
      '<li><a href="#first">First</a></li><li><a href="#work--state">Work &#38; state</a><ol><li><a href="#r%C3%A9sum%C3%A9">Résumé</a></li></ol></li><li><a href="#work--state-1">Work &#38; state</a></li>',
    );
    expect(page).toContain('<h3 id="résumé">');
    expect(indexPage(posts)).not.toContain("data-toc");
  });

  test("posts without section headings have no empty navigation", async () => {
    const dir = await blogDir({ post: {} });
    const posts = await loadPosts(dir);
    expect(postPage(posts[0], posts)).not.toContain("data-toc");
  });

  test("render linked author profiles alongside plain names without a source report", async () => {
    const dir = await blogDir({
      post: {
        front: { authors: JSON.stringify([REPORT.author, "Ada & team"]) },
      },
    });
    const posts = await loadPosts(dir);
    expect(posts[0].authors).toEqual([REPORT.author, "Ada & team"]);
    const page = postPage(posts[0], posts);
    expect(page).toContain(
      'class="author-link" href="https://example.com/researcher"',
    );
    expect(page).toContain(
      'src="https://example.com/avatar.png?size=80&#38;v=1" alt=""',
    );
    expect(page).toContain(
      "Researcher &#38; collaborator</span></a>, <span>Ada &#38; team</span>",
    );
    expect(page).not.toContain("Original report by");
    expect(page).not.toContain("Read original report");
    const [, data] =
      /<script type="application\/ld\+json">(.+?)<\/script>/.exec(page)!;
    const structured = JSON.parse(data);
    expect(structured.author).toEqual([
      {
        "@type": "Person",
        name: REPORT.author.name,
        url: REPORT.author.url,
        image: REPORT.author.avatar,
      },
      { "@type": "Person", name: "Ada & team" },
    ]);
    expect(structured).not.toHaveProperty("citation");
  });

  test("the development article credits Zhongsheng Ji with his GitHub profile", async () => {
    const posts = await loadPosts();
    const post = posts.find((post) => post.slug === "building-a13n-with-a13n")!;
    expect(post.authors).toEqual([
      expect.objectContaining({
        name: "Zhongsheng Ji",
        url: "https://github.com/Wh1isper",
        avatar: expect.stringContaining(
          "https://avatars.githubusercontent.com/u/43375501",
        ),
      }),
    ]);
    expect(postPage(post, posts)).toContain(
      'class="author-link" href="https://github.com/Wh1isper"',
    );
    expect(post.report).toBeUndefined();
  });

  test("credit the source author without replacing the adapted article's author", async () => {
    const dir = await blogDir({
      post: {
        front: { authors: "[a13n team]", report: JSON.stringify(REPORT) },
      },
    });
    const posts = await loadPosts(dir);
    const page = postPage(posts[0], posts);
    expect(page).toContain(
      'class="author-link" href="https://example.com/researcher"',
    );
    expect(page).toContain(
      'src="https://example.com/avatar.png?size=80&#38;v=1" alt=""',
    );
    expect(page).toContain(
      "Original report by</span> Researcher &#38; collaborator",
    );
    expect(page).toContain(
      'href="https://example.com/report">Read original report',
    );
    const [, data] =
      /<script type="application\/ld\+json">(.+?)<\/script>/.exec(page)!;
    expect(JSON.parse(data)).toMatchObject({
      author: [{ "@type": "Organization", name: "a13n team" }],
      citation: {
        "@type": "DiscussionForumPosting",
        headline: REPORT.title,
        url: REPORT.url,
        author: {
          "@type": "Person",
          name: REPORT.author.name,
          url: REPORT.author.url,
        },
      },
    });
  });

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
