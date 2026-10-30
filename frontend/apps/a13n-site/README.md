# a13n site

The private landing page and blog at `https://a13n.dev/`. They are Vite and TypeScript pages without a UI framework. The documentation from `apps/a13n-docs` is published under the `/docs/` path.

## Local development

From the repository root:

```bash
make site-serve
make site-build
```

`make site-serve` serves the landing page and the blog. `make site-build` builds the documentation and the landing page, and assembles the public site in `frontend/apps/a13n-site/dist`; `pnpm --dir frontend --filter a13n-site preview` serves it.

## Structure

- `index.html` holds the page copy; `404.html` is the site-wide not-found page.
- `src/content.ts` holds the data the scenes show: agents, calls, and the provider stack. Provider and product icons come from the `a13n-ui` brand registry.
- `src/hero.ts` folds and unfolds the hero name.
- `src/stars.ts` shows the repository's GitHub star count beside the nav's GitHub link. It reads the count from Shields, which caches GitHub, so visitors never meet GitHub's per-address API limit.
- `src/scene/` holds the Service scene in three acts: `build.ts`, `call.ts`, and `run.ts`. `stage.ts` maps the scroll position to the acts and their transitions.
- `src/main.ts` shows the hero once the display face loads, then loads `src/page.ts`, which mounts everything below the hero. One frame loop drives both.
- `posts/` holds the blog posts, one folder per post, written as [Blog Posts](../../../CONTRIBUTING.md#blog-posts) describes.
- `blog/` renders the posts. `posts.ts` reads and checks each post, `markdown.ts` renders its body with the documentation site's code palette from `a13n-ui/code-theme`, `pages.ts` writes the index and post pages, and `plugin.ts` adds them to the build as HTML entries and renders them on each request in development. `src/blog.ts` runs on those pages: copy buttons, heading links, plate labels, the cover crop, and the reading progress line.

`index.html` preloads the two faces above the fold, and `public/_headers` lets browsers cache hashed assets for a year. Reduced-motion preferences stop the loops and make transitions instant. The `Site` workflow checks, tests, builds, and deploys the site to the `a13n-site` Cloudflare Pages project.

## Sitemaps

`https://a13n.dev/sitemap.xml` lists every indexable page across the landing application, the blog, and the English and Chinese documentation. `public/robots.txt` declares this single entry point. After assembling both applications, `make site-build` reads canonical URLs from their HTML with [`parse5`](https://github.com/inikulin/parse5) and generates XML with the MIT-licensed [`sitemap`](https://github.com/ekalinin/sitemap.js) package. No URL lists or XML files are maintained by hand. Pages marked `noindex` and HTML redirects are excluded. Modification dates are omitted because the build does not track each page's content modification time.

The build rejects missing or mismatched canonical URLs, duplicate entries, incomplete documentation exports, and sitemaps exceeding 50,000 URLs or 50 MB. It parses the generated XML to verify coverage and checks the root declaration in `robots.txt`. Next.js continues to publish `/docs/sitemap.xml` for existing consumers; the build verifies that it matches the documentation subset of the root sitemap. To check an existing build without regenerating it, run `pnpm --dir frontend --filter a13n-site run check:sitemap`.

Local previews serve the deployment artifact, so sitemap URLs retain the production domain `https://a13n.dev`. Inspect the full local inventory at `/sitemap.xml`; following a listed URL opens the production page. A browser's message that the XML has no style information is normal.

## Documentation entry redirects

`public/_redirects` restores known documentation URLs that omit `/docs/` with permanent redirects to their existing canonical pages. Each mapping names a concrete destination; unknown URLs, API examples, and local file paths keep the site's not-found response. Cloudflare Pages applies these rules after deployment; the Vite preview does not emulate them. Check redirect destinations against the assembled `dist/docs/` export when changing a mapping.
