import { readFile } from "node:fs/promises";
import { join, resolve } from "node:path";
import type { Plugin } from "vite";
import { indexPage, postPage } from "./pages.ts";
import { loadPosts, POSTS_DIR, type Post } from "./posts.ts";

// The image types a post may serve beside its page
const TYPES: Record<string, string> = {
  gif: "image/gif",
  jpg: "image/jpeg",
  jpeg: "image/jpeg",
  png: "image/png",
  svg: "image/svg+xml",
  webp: "image/webp",
};

/** Each blog page by its path under the site root, such as blog/index.html. */
function pages(posts: Post[]) {
  return new Map([
    ["blog/index.html", indexPage(posts)],
    ...posts.map(
      (post) =>
        [`blog/${post.slug}/index.html`, postPage(post, posts)] as const,
    ),
  ]);
}

/**
 * Renders /blog/ and a page per post from the site's posts/ directory.
 * A build adds them as HTML entries; the dev server renders them on each request.
 */
export function blog(): Plugin {
  let root = process.cwd();
  let posts: Post[] = [];
  let built = new Map<string, string>();

  return {
    name: "a13n:blog",
    async config(config, { command }) {
      root = resolve(config.root ?? root);
      if (command !== "build") return;
      posts = await loadPosts();
      built = pages(posts);
      // Vite appends these to the configured entries
      const input = [...built.keys()].map((path) => resolve(root, path));
      return { build: { rolldownOptions: { input } } };
    },
    resolveId(id) {
      if (built.has(id.slice(root.length + 1))) return id;
    },
    load(id) {
      return built.get(id.slice(root.length + 1));
    },
    async generateBundle() {
      for (const post of posts)
        for (const name of post.files)
          this.emitFile({
            type: "asset",
            fileName: `blog/${post.slug}/${name}`,
            source: await readFile(join(POSTS_DIR, post.slug, name)),
          });
    },
    configureServer(server) {
      server.watcher.on("all", (_event, path) => {
        if (path.startsWith(POSTS_DIR)) server.ws.send({ type: "full-reload" });
      });
      server.middlewares.use(async (req, res, next) => {
        const path = new URL(req.url ?? "/", "http://localhost").pathname;
        if (!/^\/blog(\/|$)/.test(path)) return next();
        try {
          if (!path.endsWith("/") && !/\.[a-z0-9]+$/i.test(path)) {
            res.writeHead(301, { Location: `${path}/` }).end();
            return;
          }
          const fresh = await loadPosts();
          const file = /^\/blog\/([^/]+)\/([^/]+)$/.exec(path);
          if (
            file &&
            fresh.some((p) => p.slug === file[1] && p.files.includes(file[2]))
          ) {
            const type = TYPES[file[2].split(".").pop()!.toLowerCase()];
            if (type) res.setHeader("Content-Type", type);
            res.end(await readFile(join(POSTS_DIR, file[1], file[2])));
            return;
          }
          const html = pages(fresh).get(`${path.slice(1)}index.html`);
          if (!html) return next();
          res.setHeader("Content-Type", "text/html");
          res.end(await server.transformIndexHtml(path, html));
        } catch (error) {
          next(error);
        }
      });
    },
  };
}
