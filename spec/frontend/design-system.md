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

| Role         | Token                    | Use                                                                         |
| ------------ | ------------------------ | --------------------------------------------------------------------------- |
| Canvas       | `--a13n-canvas`          | Page background                                                             |
| Surface      | `--a13n-surface`         | Grouping regions, list rows, rails, tiles, code blocks, empty states        |
| Elevated     | `--a13n-elevated`        | Popovers, dialogs, icon tiles and controls placed on a surface              |
| Hover        | `--a13n-hover`           | Interactive row and tile hover                                              |
| Selected     | `--a13n-selected`        | Active navigation and selected list rows                                    |
| Hairline     | `--a13n-separator`       | Thin dividers, lighter than control borders                                 |
| Soft border  | `--a13n-border-soft`     | Quiet control and surface boundaries                                        |
| Input border | `--a13n-input-border`    | Input, Textarea, Select triggers                                            |
| Text         | `--a13n-text`            | Primary text                                                                |
| Secondary    | `--a13n-secondary`       | Supporting body text and descriptions; at least 7:1 on canvas               |
| Tertiary     | `--a13n-tertiary`        | Metadata, timestamps and group labels; at least 4.5:1 on canvas             |
| Quaternary   | `--a13n-quaternary`      | Placeholders, disabled labels and decorative glyphs; at least 3:1 on canvas |
| Signal       | `--a13n-signal`          | Focus rings, text selection, and the first chart series                     |
| Raised       | `--a13n-shadow-subtle`   | Icon tiles and controls on a surface                                        |
| Overlay      | `--a13n-shadow`          | Popovers, menus, selects, and tooltips                                      |
| Floating     | `--a13n-shadow-floating` | Dialogs and floating bars                                                   |

Use semantic tokens rather than local shades. Product guides own palette and radius values. Floating content uses overlay elevation; ordinary hover does not imply elevation. A row has one hover/selected surface; selection remains distinct without moving text. Dark mode preserves hierarchy through its own fills; native controls and syntax colors follow the selected theme.

The four text roles remain visibly distinct in both themes. `--a13n-text-supporting` aliases secondary; `--a13n-muted` aliases tertiary. Tailwind's `muted-foreground` maps to tertiary, not secondary. Do not lighten secondary or tertiary with opacity when they carry readable content. Placeholder and disabled styles use quaternary directly rather than an additional alpha blend.

The shared palette maps secondary to `--text-secondary` (`#4f525b` light / `#b9bcc4` dark), tertiary to `--muted-foreground` (`#696c74` / `#979aa2`) and quaternary to `--text-quaternary` (`#858891` / `#747780`). Primary is `--foreground` (`#16171a` / `#ececee`), on canvas `#ffffff` / `#111214`. Product palette overrides preserve the same role separation and contrast floors.

Materials are progressive enhancements. `--a13n-material-bar` supplies header, toolbar and sidebar backgrounds (86% canvas); `--a13n-material-overlay` supplies menu, popover, select and sheet backgrounds (94% popover surface). Apply `--a13n-material-filter` to `backdrop-filter` and its WebKit equivalent: `saturate(180%) blur(20px)`. Without filter support, or with `prefers-reduced-transparency: reduce`, these backgrounds are opaque and the filter is `none`. Text remains opaque. Dark overlay and floating shadows combine a deeper cast shadow with a 1px inset light ring; raised surfaces retain a quieter inset ring. No material or shadow is added on ordinary row hover.

Shared list rhythm uses `--a13n-row-height` (32px), `--a13n-row-height-lg` (44px, two-line/palette row), `--a13n-row-height-touch` (44px minimum), `--a13n-row-gap` (2px), `--a13n-group-gap` (16px) and `--a13n-inset` (8px horizontal row inset). Product layouts can grow rows for content, but do not shrink touch targets. Control-specific geometry remains product-owned.

## Typography and Identity

Use system sans-serif and the product's type scale. De-emphasize supporting text through color or weight before adding a size. Monospace belongs to code, JSON and commands; ordinary identifiers use sans-serif. Compared numbers use tabular figures.

Use shared `font` shorthand tokens as `font: var(--a13n-font-label)`. Color is applied separately. Defaults use the existing whole-pixel scale and system stack; product size preferences may scale the underlying steps.

| Token                     | Default weight / size / line height | Role                            |
| ------------------------- | ----------------------------------- | ------------------------------- |
| `--a13n-font-large-title` | 600 / 24px / 1.2                    | Page titles                     |
| `--a13n-font-title`       | 600 / 17px / 1.4                    | Pane and dialog titles          |
| `--a13n-font-headline`    | 500 / 14px / 1.45                   | Emphasized row titles           |
| `--a13n-font-body`        | 400 / 14px / 1.6                    | Body text                       |
| `--a13n-font-callout`     | 400 / 13px / 1.5                    | Secondary body and descriptions |
| `--a13n-font-label`       | 500 / 12px / 1.4                    | Section/group and field labels  |
| `--a13n-font-caption`     | 400 / 12px / 1.4                    | Metadata, timestamps and hints  |

Generic settings group and picker/menu labels use the label style in tertiary; field and disclosure labels use it in secondary. Descriptions use callout/secondary; compact metadata and disclosure summaries use caption/tertiary. Labels stay closer to their content than to the previous group.

Use sentence case without decorative tracking; preserve proper names, acronyms and literal protocol values. Device codes may use tracking for character-by-character reading. Peer controls use consistent translated terms.

Icons match neighboring type. Brand marks and names remain adjacent, using the shared registry and fallback. Resource image fallbacks use initials from the first and last name words and identifier-derived colors with readable contrast. Activity animation respects reduced motion.

## Color and Status

Neutral surfaces carry the interface. Primary buttons are near-black in light mode and near-white in dark mode. Indigo signals focus, selection, prose links and charts, not decorative fills. Semantic colors are success (emerald), warning (amber), danger (red), info (blue) and neutral (gray); labels identify states without color alone. Product guides own link and status treatment. Internal navigation stays in the product; external destinations use the appropriate affordance.

## Identity Color

Identity is separate from status. A child conversation uses a deterministic generated glyph: a rounded polygon with a pair of eyes. A stable hash of its Thread ID seeds every variable feature: palette entry, side count, rotation, vertex and corner variation, eye style, gaze and tilt. State never changes the glyph or its hue. Bodies keep similar visual weight, eyes stay legible at 16px and glyphs retain their proportions at tab/header sizes. State uses text, a badge or reduced-motion-aware animation. Tool icons remain neutral.

Harness owns `--a13n-identity-1` through `--a13n-identity-8`: clean, saturated candy light/dark pairs `#ff6fb1`/`#ff8cc2`, `#ff8a5c`/`#ffa37f`, `#ffc53d`/`#ffd36b`, `#8ed94f`/`#a7e472`, `#33d1a0`/`#5bdcb4`, `#3cc8f0`/`#6fd8f6`, `#6b8cff`/`#8fa8ff`, and `#a77bff`/`#bb98ff`. `--a13n-identity-ink` (`#231f33`) keeps eyes legible and `--a13n-identity-light` (`#fff`) supplies eye whites and highlights. Identity colors never become control fills or success/failure signals; names remain ordinary text.

## Controls, Overlays and Feedback

- Click actions use `pointer`; text entry and spatial drag/resize controls retain their appropriate cursors. Disabled controls do not advertise activation. Shared styles own semantic defaults, including portals; cursor styling does not replace keyboard semantics.
- Fields and icon-only controls have accessible names and visible focus: a 2px signal outline with 2px offset on keyboard focus, never an added mouse-click ring. Disabled primary and secondary actions use a quiet surface and muted text; disabled ghost/icon actions remain unfilled. Loading preserves geometry and blocks duplicate activation. Tooltips add context rather than repeat familiar labels; consequences remain visible beside their action.
- Popovers and menus use the overlay material and overlay shadow; dialogs use the floating shadow. Popup width is independent of its trigger and bounded by the viewport. Dialog bodies scroll with actions reachable, with one owner for each inset. Product guides own dimensions and motion.
- Disclosures preserve drafts. A dialog's originating menu stays hidden through entry and exit; focus restoration does not reopen it.
- Isolated action results and dismissible reminders use a top-center notification deck. At most three active cards remain; bursts replace the oldest cards rather than creating a hidden backlog. Each card has a one-line title, at most two preview lines, a named dismissal and an inline action when needed. The collapsed deck uses a small offset and scale; hover or keyboard focus expands it and pauses dismissal timers. Pointer drag and touch swipe dismiss cards. Non-critical notices expire; product contracts may retain input-needed notices until dismissal or replacement. Safe-area top spacing and 16px narrow-screen gutters keep the deck within the viewport. Persistent recovery has one owner in the affected view; field validation stays beside its field. A failed view replaces dependent loading/error UI while preserving unsent input. Product contracts own exact placement and recovery semantics.
- Tooltips use a compact dark surface in both themes, wrap within 320px and the viewport, open after 150ms, close after 80ms and enter over the fast motion duration. Reduced motion removes animation. Reference popovers retain selectable values and a connected hover path with a short closing delay; ID/Key labels copy their value with a “Click to copy” hint.

## Scrolling and States

`ScrollArea` and `a13n-scrollbar` share tokens. Fine pointers use thin, quiet thumbs revealed on hover/focus or while scrolling where the primitive supports it. Coarse pointers use native overlay scrolling, never visible custom thumbs. Use a content-sized `ScrollArea` for bounded readers and pickers. Applications can opt native scroll owners into the same thumb treatment with `a13n-scrollbars` on the document root. Textareas, pickers and readers bound their own scrolling; ordinary pages use their product's page-scroll anatomy. Fixed controls never hide essential content. Long names and narrow viewports retain readable values and reachable actions.

Skeletons preserve destination geometry; spinners belong to gates and explicit actions. Refresh keeps existing content. Empty/error states align within their owning column. Revealing actions, expanding rows and replacing loading states do not cause unrelated horizontal shifts or discard reading position.

## Accessibility, Motion and Theme

Regions identify themselves through headings. Touch does not depend on hover; compact visuals retain adequate targets for the input modality. Width changes layout, while pointer capability determines touch behavior.

Motion uses `--a13n-duration-fast` / `--a13n-duration-base` / `--a13n-duration-slow` (120/200/320ms) and `--a13n-ease-out` / `--a13n-ease-in` / `--a13n-ease-in-out` / `--a13n-ease-sheet`. `--a13n-ease` aliases out; `--a13n-overlay-duration` aliases base. Entry eases out; exit is faster and eases in. `--a13n-ease-spring` with `--a13n-duration-spring` (500ms) has a small, cleanly settling overshoot for interruptible sheet/pane settling. `--a13n-ease-spring-snappy` with `--a13n-duration-spring-snappy` (300ms) approaches rest without overshoot for menu, popover and select scale entry, while opacity enters over the base duration. Both spring curves use CSS `linear()` stops and fall back to ease-out when unsupported. Exits use fast/ease-in rather than reversing a spring. Menus, popovers, selects and combobox surfaces fade and scale from 0.96 at the trigger-side transform origin. Sheets slide with the sheet curve; dialog motion remains product-owned. Disclosures use a 0fr-to-1fr grid rather than measured-height animation. Reduced motion collapses all five duration tokens to 1ms. Only transform and opacity animate, apart from disclosure grid rows; incidental dimensions, padding and weight animation are not permitted. Press feedback changes fill/color immediately without moving the control.

Load shared styles once and use `a13n-root` for base typography. Light is the default; document-level `dark` makes portals inherit the theme. Console and Harness WebUI each own their viewer-local display-language choice; their product contracts define localization coverage. Shared components receive application-owned strings.

## Documentation Pages

The documentation site is a reading surface, so its pages set type for sustained reading rather than interface density. Prose is 16px with a 1.7 line height in supporting text (`--a13n-text-supporting`), so headings, bold text, links, and code stand out in text color. Page titles are 30px, section headings 22px, and subsection headings 18px, all at weight 600, which prose also uses for bold text; a hairline above each section heading divides the page. The description beneath the title is 18px secondary text. The reading column is at most 800px wide, centered between the navigation and the table of contents. The navigation sits on the canvas without a tint: rows keep the 32px height and 8px radius but use text color, and group labels are 13px at weight 600 in text color. The copy and open page actions follow the table of contents when it is shown and otherwise sit beside the title.

Hue appears in page content only where it carries meaning. Links keep the text color at weight 500 over a signal underline that strengthens on hover. Inline code in running text is a chip on the surface with a 6px radius and an input-border outline; in headings and controls it is bare monospace. Table headers sit on a rounded surface band in 12px secondary text at weight 600. Code blocks have a hairline frame on the surface and a title bar naming the file, or the language when no file is given; plain text and output keep a bare frame. Code tokens use one vivid palette in both themes: keywords pink, functions and commands violet, types teal, keys red, strings green, and literals orange, with comments and punctuation gray. Callouts are surfaces tinted with their kind's hue: note info, tip success, important violet, warning amber, and caution red.

Diagrams sit on a surface panel with a dot grid that fades toward its edges. Nodes, states, and sequence participants are cards with a hairline ring and a soft shadow; groups and sequence frames are hairline outlines with their titles at the top-left, edges are thin with small arrowheads, and edge labels are masked by the panel. Wrapped labels break into lines of even length. A diagram source may mark node categories with `class Node <category>`: `a13n` (violet) for Agent Foundation components, `app` (teal) for people and calling applications, `store` (orange) for state and storage, `ext` (pink) for external models, services, and tools, and `success`, `warning`, and `danger` for outcomes. A category tints the card, its ring, and its label. Sequence diagrams have no class statement, so they write the same line as a comment, `%% class Node <category>`; the participant's lifeline, its messages, and the numbered step at the start of each message take its color. GitHub renders the same source without these colors, so a diagram never depends on them.

## Development Showcase

`a13n-ui/dev` demonstrates primitives and compositions through its own Vite entry. It does not prove product-page composition. Package checks cover types, interactions and the showcase; app builds verify consumption. Choose checks under [Local Validation](../../CONTRIBUTING.md#local-validation), not a mandatory full visual matrix.
