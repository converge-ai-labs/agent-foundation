import type { Post } from "./posts.ts";

const ORIGIN = "https://a13n.dev";
const GITHUB = "https://github.com/converge-ai-labs/agent-foundation";
const SOCIAL_IMAGE = `${ORIGIN}/social-preview.png`;
const BLOG = "a13n blog";

const esc = (value: string) =>
  value.replace(/[&<>"']/g, (c) => `&#${c.charCodeAt(0)};`);

const day = (date: string, month: "short" | "long" = "short") =>
  new Date(`${date}T00:00:00Z`).toLocaleDateString("en-US", {
    year: "numeric",
    month,
    day: "numeric",
    timeZone: "UTC",
  });

export const postPath = (post: Post) => `/blog/${post.slug}/`;

interface Head {
  title: string;
  description: string;
  path: string;
  type: "website" | "article";
  data: object;
}

function htmlPage(head: Head, body: string, current: "page" | "true") {
  const url = `${ORIGIN}${head.path}`;
  // "<" is escaped so a title can never close the script element
  const data = JSON.stringify({
    "@context": "https://schema.org",
    ...head.data,
  }).replace(/</g, "\\u003c");
  return `<!doctype html>
<html lang="en">
  <head>
    <meta charset="utf-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>${esc(head.title)}</title>
    <meta name="description" content="${esc(head.description)}" />
    <meta name="theme-color" content="#fbfbfd" />
    <link rel="canonical" href="${url}" />
    <script type="application/ld+json">${data}</script>
    <meta property="og:site_name" content="a13n" />
    <meta property="og:type" content="${head.type}" />
    <meta property="og:url" content="${url}" />
    <meta property="og:title" content="${esc(head.title)}" />
    <meta property="og:description" content="${esc(head.description)}" />
    <meta property="og:image" content="${SOCIAL_IMAGE}" />
    <meta property="og:image:width" content="1200" />
    <meta property="og:image:height" content="630" />
    <meta name="twitter:card" content="summary_large_image" />
    <meta name="twitter:image" content="${SOCIAL_IMAGE}" />
    <link rel="icon" type="image/svg+xml" href="/node_modules/a13n-ui/src/brand/a13n-logo.svg" />
    <link rel="preload" as="font" type="font/woff2" crossorigin href="/node_modules/@fontsource-variable/space-grotesk/files/space-grotesk-latin-wght-normal.woff2" />
    <link rel="preload" as="font" type="font/woff2" crossorigin href="/node_modules/@fontsource-variable/inter/files/inter-latin-wght-normal.woff2" />
    <script type="module" src="/src/blog.ts"></script>
  </head>
  <body>
    <header class="nav solid">
      <a class="brand" href="/" aria-label="a13n home">
        <span data-mark="20"></span>
        <span class="brand-word">a13n</span>
      </a>
      <nav class="nav-links" aria-label="Primary">
        <a href="/docs/">Docs</a>
        <a href="/blog/" aria-current="${current}">Blog</a>
        <a href="${GITHUB}" data-stars>GitHub</a>
      </nav>
    </header>
${body}
    <footer class="foot">
      <div class="wrap foot-row">
        <img src="/src/assets/converge-ai-lockup.svg" alt="Converge AI" />
        <nav aria-label="Footer">
          <a href="/docs/">Docs</a>
          <a href="/blog/">Blog</a>
          <a href="${GITHUB}">GitHub</a>
          <span>Apache 2.0</span>
        </nav>
      </div>
    </footer>
  </body>
</html>
`;
}

const plate = (post: Post) =>
  post.cover ? `<div class="plate">${post.cover}</div>` : "";

const meta = (post: Post) =>
  `<span class="meta"><time datetime="${post.date}">${day(post.date)}</time><i></i>${post.minutes} min</span>`;

const card = (post: Post, level: 2 | 3) => `<li class="item">
          <a href="${postPath(post)}">
            ${plate(post)}
            <h${level} class="i-title">${esc(post.title)}</h${level}>
            <p class="i-dek">${esc(post.description)}</p>
            ${meta(post)}
          </a>
        </li>`;

export function indexPage(posts: Post[]) {
  const [lead, ...rest] = posts;
  // the nav links every page to /blog/, so the index needs a post to lead
  if (!lead) throw new Error("posts/ has no posts");
  const description =
    "Notes from the team building a13n, the open-source foundation for AI agents.";
  const body = `    <main>
      <header class="wrap head">
        <h1 class="statement">Blog. <span class="dim">Notes from the team building a13n.</span></h1>
      </header>
      <div class="wrap">
        <a class="lead" href="${postPath(lead)}">
          ${plate(lead)}
          <span class="l-text">
            <h2 class="l-title">${esc(lead.title)}</h2>
            <p class="l-dek">${esc(lead.description)}</p>
            ${meta(lead)}
          </span>
        </a>
      </div>${
        rest.length
          ? `
      <ol class="wrap grid">
        ${rest.map((post) => card(post, 2)).join("\n        ")}
      </ol>`
          : ""
      }
    </main>`;
  return htmlPage(
    {
      title: "Blog — a13n",
      description,
      path: "/blog/",
      type: "website",
      data: {
        "@type": "Blog",
        name: BLOG,
        description,
        url: `${ORIGIN}/blog/`,
      },
    },
    body,
    "page",
  );
}

export function postPage(post: Post, posts: Post[]) {
  const others = posts.filter((other) => other !== post).slice(0, 3);
  const body = `    <span class="progress" aria-hidden="true"></span>
    <main>
      <article>
        <header class="wrap ph">
          <a class="back link" href="/blog/"><span class="arr-l" aria-hidden="true">←</span>Blog</a>
          <h1 class="p-title">${esc(post.title)}</h1>
          <p class="p-dek">${esc(post.description)}</p>
          <div class="strip">
            <time datetime="${post.date}">${day(post.date, "long")}</time>
            <span>${post.minutes} min read</span>
            <span>${post.authors.map(esc).join(", ")}</span>
          </div>
        </header>${
          post.cover
            ? `
        <div class="wrap cover"><div class="plate bare">${post.cover}</div></div>`
            : ""
        }
        <div class="prose col">
${post.html}
        </div>
      </article>${
        others.length
          ? `
      <section class="wrap keep">
        <h2 class="keep-head">Keep reading</h2>
        <ol class="grid">
          ${others.map((other) => card(other, 3)).join("\n          ")}
        </ol>
      </section>`
          : ""
      }
    </main>`;
  return htmlPage(
    {
      title: `${post.title} — a13n`,
      description: post.description,
      path: postPath(post),
      type: "article",
      data: {
        "@type": "BlogPosting",
        headline: post.title,
        description: post.description,
        datePublished: post.date,
        author: post.authors.map((name) => ({ "@type": "Person", name })),
        image: SOCIAL_IMAGE,
        url: `${ORIGIN}${postPath(post)}`,
        isPartOf: { "@type": "Blog", name: BLOG, url: `${ORIGIN}/blog/` },
      },
    },
    body,
    "true",
  );
}
