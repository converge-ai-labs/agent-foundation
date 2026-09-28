# Brand assets

`SpaceGrotesk-Bold.woff2` is the unmodified Space Grotesk Bold font from [Florian Karsten Studio](https://github.com/floriankarsten/space-grotesk), revision `03507d024a01282884232081fc6011c09ff4e849`, at `fonts/woff2/static/SpaceGrotesk-Bold.woff2`. Its SIL Open Font License is included in `SpaceGrotesk-OFL.txt`.

Use `Wordmark` for the a13n brand name and `Logo` for its symbol. `LogoAnimation` is a decorative looping form of the symbol: it turns 60° per cycle while its shadow follows slightly behind, and reduced-motion preferences leave it still. Callers that show it for loading supply their own status text. `--a13n-font-brand` exposes the brand typeface for other brand typography. Applications control scale and placement; the shared wordmark owns weight and spacing.

Use `BrandIcon` for product and provider identities shared across applications. It resolves canonical identities, aliases, and exact endpoint hosts through the exported `brands` registry, supports light and dark variants, and falls back from failed remote assets to a caller-provided HTTPS logo and then a generic icon. The registry is display-only and never selects endpoints or accounts.

The registry includes the complete LobeHub Icons catalog through version-pinned jsDelivr URLs; SVG files are not copied into this repository. `lobe-brands.generated.ts` records the generated mappings from `@lobehub/icons@5.18.0` to `@lobehub/icons-static-svg@1.95.0`. Run `pnpm --dir frontend --filter a13n-ui sync:brands` after updating the pinned versions in the generator, then review and test the generated diff. LobeHub Icons is distributed under the MIT License; its product and provider marks remain subject to their owners' trademark terms.
