# Documentation theme

`mkdocs.yml` uses this small Material adapter to align the documentation with the current `a13n-ui` frontend. Markdown content stays under `docs/`; assets and templates stay here.

## Sources

- `frontend/packages/a13n-ui/src/styles/theme.css` owns the Coss neutral light/dark colors. `theme.css` maps those semantic values to Material variables, with larger documentation typography and accessible indigo links derived from the brand palette. It is an adapter, not another frontend theme.
- `hooks.py` bundles the shared `LICENSE.coss` beside the generated theme and preserves its complete text in the CSS output.
- `frontend/packages/a13n-ui/src/styles/tokens.css` owns the application font/token aliases and is included unchanged. The adapter exposes the active semantic palette on `:root` as well as Material's `body`, so root-scoped aliases resolve in both themes.
- `frontend/packages/a13n-ui/src/brand` owns the logo, Space Grotesk Bold font, and font license. `hooks.py` copies them into the built site directly from those sources. No duplicate brand binaries are committed here.
- `.icons/phosphor` under `theme/` contains selected regular-weight SVGs rendered from the frontend's **@phosphor-icons/react 2.1.10** public components. The 256px view box and path geometry are unchanged; SVGs use `currentColor` at a nominal 24px size. `phosphor-LICENSE` contains the MIT notice and ships with the site.

When changing the shared frontend theme, update the small Material semantic bridge as part of the same visual check. When changing the frontend Phosphor version, refresh the selected SVGs using React's `renderToStaticMarkup` with `weight="regular"` and `size={24}`, and retain the license. Documentation builds require only the Python workspace, not Node.js, a frontend build, or live icon/font downloads.

The Mermaid fence formatter supplies flowchart and sequence sizing through diagram frontmatter, keeping SVGs at their natural width inside a horizontally scrollable region rather than shrinking labels to fit. Markdown fences keep their graph source only; the formatter owns this presentation configuration.

The adapter retains Material's navigation, search, code copying, and content scrolling. It supplies medium-weight headings, a bounded reading canvas, neutral navigation selection, semantic callouts, and shared scrollbar colors. State selectors deliberately cover Material's hover, open, focus, and narrow-screen rules; changing a base selector alone may not change the rendered state.

## Validation

```console
make docs-build
uv run --locked pytest scripts/tests/test_docs.py
make docs-serve
```

Check light and dark themes, the mobile drawer and search (including results and no results), readable code/table overflow, keyboard focus and copying, and rendered Mermaid SVGs. Material renders diagrams in a closed shadow root: inspect the rendered diagram rather than expecting an outer `.mermaid svg` selector to reach it. Strict MkDocs checks generated HTML and navigation, not Mermaid's browser parser. Material loads its Mermaid runtime in the browser; graph checks need that runtime to be reachable.
