# a13n site

The private landing page at `https://a13n.dev/`. It is a Vite and TypeScript page without a UI framework. The documentation from `apps/a13n-docs` is published under its `/docs/` path.

## Local development

From the repository root:

```bash
make site-serve
make site-build
```

`make site-serve` serves the landing page alone. `make site-build` builds the documentation and the landing page, and assembles the public site in `frontend/apps/a13n-site/dist`; `pnpm --dir frontend --filter a13n-site preview` serves it.

## Structure

- `index.html` holds the page copy; `404.html` is the site-wide not-found page.
- `src/content.ts` holds the data the scenes show: agents, calls, and the provider stack. Provider and product icons come from the `a13n-ui` brand registry.
- `src/hero.ts` folds and unfolds the hero name.
- `src/stars.ts` shows the repository's GitHub star count beside the nav's GitHub link. It reads the count from Shields, which caches GitHub, so visitors never meet GitHub's per-address API limit.
- `src/scene/` holds the Service scene in three acts: `build.ts`, `call.ts`, and `run.ts`. `stage.ts` maps the scroll position to the acts and their transitions.
- `src/main.ts` shows the hero once the display face loads, then loads `src/page.ts`, which mounts everything below the hero. One frame loop drives both.

`index.html` preloads the two faces above the fold, and `public/_headers` lets browsers cache hashed assets for a year. Reduced-motion preferences stop the loops and make transitions instant. The `Site` workflow checks, tests, builds, and deploys the site to the `a13n-site` Cloudflare Pages project.

## Sitemaps

`https://a13n.dev/sitemap.xml` lists every indexable page across the landing application and the English and Chinese documentation. `public/robots.txt` declares this single entry point. After assembling both applications, `make site-build` reads canonical URLs from their HTML with [`parse5`](https://github.com/inikulin/parse5) and generates XML with the MIT-licensed [`sitemap`](https://github.com/ekalinin/sitemap.js) package. No URL lists or XML files are maintained by hand. Pages marked `noindex` and HTML redirects are excluded. Modification dates are omitted because the build does not track each page's content modification time.

The build rejects missing or mismatched canonical URLs, duplicate entries, incomplete documentation exports, and sitemaps exceeding 50,000 URLs or 50 MB. It parses the generated XML to verify coverage and checks the root declaration in `robots.txt`. Next.js continues to publish `/docs/sitemap.xml` for existing consumers; the build verifies that it matches the documentation subset of the root sitemap. To check an existing build without regenerating it, run `pnpm --dir frontend --filter a13n-site run check:sitemap`.

Local previews serve the deployment artifact, so sitemap URLs retain the production domain `https://a13n.dev`. Inspect the full local inventory at `/sitemap.xml`; following a listed URL opens the production page. A browser's message that the XML has no style information is normal.
