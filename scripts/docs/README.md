# Documentation theme

`mkdocs.yml` uses this small Material adapter to align the documentation with the current `a13n-ui` frontend. Markdown content stays under `docs/`; assets and templates stay here.

## Sources

- `frontend/packages/a13n-ui/src/styles/theme.css` owns the Coss neutral light/dark colors. `theme.css` maps those semantic values to Material variables, with larger documentation typography and accessible indigo links derived from the brand palette. It is an adapter, not another frontend theme.
- `frontend/packages/a13n-ui/src/styles/tokens.css` owns the application font/token aliases and is included unchanged.
- `frontend/packages/a13n-ui/src/brand` owns the logo, Space Grotesk Bold font, and font license. `hooks.py` copies them into the built site directly from those sources. No duplicate brand binaries are committed here.
- `.icons/lucide` under `theme/` contains a selected SVG export from the frontend's **lucide-react 0.577.0** package. The icon node geometry, 24px view box, round caps/joins, and 2px outline are unchanged; React-only node keys are omitted. `lucide-LICENSE` includes the ISC and inherited Feather notices and ships with the site.

When changing the shared frontend theme, update the small Material semantic bridge as part of the same visual check. When changing the frontend Lucide version, refresh the selected SVGs from its public icon nodes and retain the license. Documentation builds require only the Python workspace, not Node.js, a frontend build, or live icon/font downloads.

## Validation

```console
make docs-build
uv run --locked pytest scripts/tests/test_docs.py
make docs-serve
```

Check light and dark themes, the mobile drawer and search, readable code/table overflow, focus indicators, and rendered Mermaid SVGs. Strict MkDocs checks generated HTML and navigation, not Mermaid's browser parser. Material loads its Mermaid runtime in the browser; graph checks need that runtime to be reachable.
