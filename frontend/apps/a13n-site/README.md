# a13n site

The private landing page at `https://a13n.converge.ai/`. It is a Vite and TypeScript page without a UI framework. The documentation from `apps/a13n-docs` is published under its `/docs/` path.

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
- `src/scene/` holds the Service scene in three acts: `build.ts`, `call.ts`, and `run.ts`. `stage.ts` maps the scroll position to the acts and their transitions.
- `src/main.ts` shows the hero once the display face loads, then loads `src/page.ts`, which mounts everything below the hero. One frame loop drives both.

`index.html` preloads the two faces above the fold, and `public/_headers` lets browsers cache hashed assets for a year. Reduced-motion preferences stop the loops and make transitions instant. The `Site` workflow checks, tests, builds, and deploys the site to the `a13n-site` Cloudflare Pages project.
