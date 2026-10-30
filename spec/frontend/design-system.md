# Frontend Design System

## Scope and Ownership

This document owns common visual foundations and `a13n-ui` primitives. Product guides own dimensions and page anatomy; behavior contracts own application state.

| Owner                                                                                                                                  | Scope                                                      |
| -------------------------------------------------------------------------------------------------------------------------------------- | ---------------------------------------------------------- |
| [Console design](console-design-guidelines.md) / [behavior](console.md)                                                                | Management pages, resource editors, Chat and Debug         |
| [Harness design](../a13n-harness-ui/webui/06-design-guidelines.md) / [workbench](../a13n-harness-ui/webui/04-workbench-interaction.md) | Compact workbench, conversation, settings and native panes |
| [Theme](../../frontend/packages/a13n-ui/src/styles/theme.css) / [aliases](../../frontend/packages/a13n-ui/src/styles/tokens.css)       | Shared token implementation and scrollbars                 |
| [Exports](../../frontend/packages/a13n-ui/src/index.ts) / [provenance](../../frontend/packages/a13n-ui/coss-source.json)               | Public APIs, registry revision, license and adaptations    |

Coss UI supplies primitives using Base UI and DayPicker; Tailwind styles shared components and CSS Modules own app layouts. `src/patterns` owns reusable compositions; `src/brand` owns identity assets and provider marks. Shared UI never imports applications or interprets domain states. Applications own data, persistence, translations and domain-to-status mapping. [Repository Model](../repository-model.md#frontend-workspace) owns packaging.

## Principles

- Give each page or active dialog one clear primary task and one primary action. Use size, contrast and whitespace deliberately.
- Equivalent surfaces within a product share anatomy. Product-specific patterns do not transfer automatically between Console and Harness.
- Lead with names, identity and status; keep identifiers secondary, selectable and copyable.
- Separate groups more than items; keep labels closer to their content. Align text starts and trailing controls, including optical vertical centering.
- Use boundaries for controls, floating layers or dense rows. Prefer space and tone for grouping; avoid nested frames and decorative shadows.
- Distinguish pending, saved, failed, disabled and unavailable states in words. Missing data is not zero or success.
- Disclose supporting detail progressively without hiding essential input, results, consequences or recovery behind Debug or hover.

## Tokens and Surfaces

| Role         | Token                    | Use                                                                  |
| ------------ | ------------------------ | -------------------------------------------------------------------- |
| Canvas       | `--a13n-canvas`          | Page background                                                      |
| Surface      | `--a13n-surface`         | Grouping regions, list rows, rails, tiles, code blocks, empty states |
| Elevated     | `--a13n-elevated`        | Popovers, dialogs, icon tiles and controls placed on a surface       |
| Hover        | `--a13n-hover`           | Interactive row and tile hover                                       |
| Selected     | `--a13n-selected`        | Active navigation and selected list rows                             |
| Hairline     | `--a13n-border-soft`     | Subtle separators where spacing alone is insufficient                |
| Input border | `--a13n-input-border`    | Input, Textarea, Select triggers                                     |
| Text         | `--a13n-text`            | Primary text                                                         |
| Secondary    | `--a13n-secondary`       | Descriptions, metadata, labels in rails                              |
| Tertiary     | `--a13n-tertiary`        | Counts and the quietest supporting text                              |
| Signal       | `--a13n-signal`          | Focus rings, text selection, and the first chart series              |
| Raised       | `--a13n-shadow-subtle`   | Icon tiles and controls on a surface                                 |
| Overlay      | `--a13n-shadow`          | Popovers, menus, selects, and tooltips                               |
| Floating     | `--a13n-shadow-floating` | Dialogs and floating bars                                            |

Use semantic tokens rather than local shades. Product guides own palette and radius values. Floating content uses overlay elevation; ordinary hover does not imply elevation. A row has one hover/selected surface; selection remains distinct without moving text. Dark mode preserves hierarchy through its own fills; native controls and syntax colors follow the selected theme.

## Typography and Identity

Use system sans-serif and the product's type scale. De-emphasize supporting text through color or weight before adding a size. Monospace belongs to code, JSON and commands; ordinary identifiers use sans-serif. Compared numbers use tabular figures.

Use sentence case without decorative tracking; preserve proper names, acronyms and literal protocol values. Device codes may use tracking for character-by-character reading. Peer controls use consistent translated terms.

Icons match neighboring type. Brand marks and names remain adjacent, using the shared registry and fallback. Resource image fallbacks use initials from the first and last name words and identifier-derived colors with readable contrast. Activity animation respects reduced motion.

## Color and Status

Neutral surfaces carry the interface. Primary buttons are near-black in light mode and near-white in dark mode. Indigo signals focus, selection, prose links and charts, not decorative fills. Semantic colors are success (emerald), warning (amber), danger (red), info (blue) and neutral (gray); labels identify states without color alone. Product guides own link and status treatment. Internal navigation stays in the product; external destinations use the appropriate affordance.

## Controls, Overlays and Feedback

- Click actions use `pointer`; text entry and spatial drag/resize controls retain their appropriate cursors. Disabled controls do not advertise activation. Shared styles own semantic defaults, including portals; cursor styling does not replace keyboard semantics.
- Fields and icon-only controls have accessible names and visible focus. Loading preserves geometry and blocks duplicate activation. Tooltips add context rather than repeat familiar labels; consequences remain visible beside their action.
- Popovers and menus use the elevated surface and overlay shadow; dialogs use the floating shadow. Popup width is independent of its trigger and bounded by the viewport. Dialog bodies scroll with actions reachable, with one owner for each inset. Product guides own dimensions and motion.
- Disclosures preserve drafts. A dialog's originating menu stays hidden through entry and exit; focus restoration does not reopen it.
- Isolated action results and dismissible reminders use top-center toasts. Persistent recovery has one owner in the affected view; field validation stays beside its field. A failed view replaces dependent loading/error UI while preserving unsent input. Product contracts own exact placement and recovery semantics.
- Tooltips use a compact dark surface in both themes, wrap within 320px and the viewport, open after 150ms, close after 80ms and fade over 100ms. Reduced motion removes animation. Reference popovers retain selectable values and a connected hover path with a short closing delay; ID/Key labels copy their value with a “Click to copy” hint.

## Scrolling and States

`ScrollArea` and `a13n-scrollbar` share tokens. Textareas, pickers and readers bound their own scrolling; ordinary pages use their product's page-scroll anatomy. Fixed controls never hide essential content. Long names and narrow viewports retain readable values and reachable actions.

Skeletons preserve destination geometry; spinners belong to gates and explicit actions. Refresh keeps existing content. Empty/error states align within their owning column. Revealing actions, expanding rows and replacing loading states do not cause unrelated horizontal shifts or discard reading position.

## Accessibility, Motion and Theme

Regions identify themselves through headings. Touch does not depend on hover; compact visuals retain adequate targets for the input modality. Width changes layout, while pointer capability determines touch behavior.

Motion is brief, typically 100–200ms, respects reduced motion and preserves layout geometry. Product guides distinguish press and disclosure treatments; incidental width, padding and weight animation are not permitted.

Load shared styles once and use `a13n-root` for base typography. Light is the default; document-level `dark` makes portals inherit the theme. Console owns its display-language choice; Harness remains English-only. Shared components receive application-owned strings.

## Documentation Pages

The documentation site is a reading surface, so its pages set type for sustained reading rather than interface density. Prose is 16px with a 1.7 line height in supporting text (`--a13n-text-supporting`), so headings, bold text, links, and code stand out in text color. Page titles are 30px, section headings 22px, and subsection headings 18px, all at weight 600, which prose also uses for bold text; a hairline above each section heading divides the page. The description beneath the title is 18px secondary text. The reading column is at most 800px wide, centered between the navigation and the table of contents. The navigation sits on the canvas without a tint: rows keep the 32px height and 8px radius but use text color, and group labels are 13px at weight 600 in text color. The copy and open page actions follow the table of contents when it is shown and otherwise sit beside the title.

Hue appears in page content only where it carries meaning. Links keep the text color at weight 500 over a signal underline that strengthens on hover. Inline code in running text is a chip on the surface with a 6px radius and an input-border outline; in headings and controls it is bare monospace. Table headers sit on a rounded surface band in 12px secondary text at weight 600. Code blocks have a hairline frame on the surface and a title bar naming the file, or the language when no file is given; plain text and output keep a bare frame. Code tokens use one vivid palette in both themes: keywords pink, functions and commands violet, types teal, keys red, strings green, and literals orange, with comments and punctuation gray. Callouts are surfaces tinted with their kind's hue: note info, tip success, important violet, warning amber, and caution red.

Diagrams sit on a surface panel with a dot grid that fades toward its edges. Nodes, states, and sequence participants are cards with a hairline ring and a soft shadow; groups and sequence frames are hairline outlines with their titles at the top-left, edges are thin with small arrowheads, and edge labels are masked by the panel. Wrapped labels break into lines of even length. A diagram source may mark node categories with `class Node <category>`: `a13n` (violet) for Agent Foundation components, `app` (teal) for people and calling applications, `store` (orange) for state and storage, `ext` (pink) for external models, services, and tools, and `success`, `warning`, and `danger` for outcomes. A category tints the card, its ring, and its label. Sequence diagrams have no class statement, so they write the same line as a comment, `%% class Node <category>`; the participant's lifeline, its messages, and the numbered step at the start of each message take its color. GitHub renders the same source without these colors, so a diagram never depends on them.

## Development Showcase

`a13n-ui/dev` demonstrates primitives and compositions through its own Vite entry. It does not prove product-page composition. Package checks cover types, interactions and the showcase; app builds verify consumption. Choose checks under [Local Validation](../../CONTRIBUTING.md#local-validation), not a mandatory full visual matrix.
