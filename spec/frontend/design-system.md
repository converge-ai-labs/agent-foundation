# Frontend Design System

## Design Position and Ownership

`frontend/packages/a13n-ui` provides shared React primitives, compositions, and design tokens for browser applications. The interface is quiet and precise: hierarchy comes from spacing, type weight, and soft surfaces rather than lines, boxes, and decoration. The product should feel like a mature SaaS console, where every screen follows one anatomy, every control explains itself before activation, and nothing on the page competes with the user's task.

Coss UI registry components provide the interaction foundation, using Base UI for focus, overlays, selection, and menu semantics and DayPicker for calendars. Tailwind CSS owns shared component styling and semantic theme utilities. CSS Modules own application layouts. The [repository model](../repository-model.md#frontend-workspace) owns workspace and packaging boundaries.

Product layouts, navigation, conversation rendering, protocol state, data fetching, persistence, and translations belong to applications. Shared components do not import applications or interpret domain states. Badge variants describe visual meaning; applications map domain states to variants and translated labels.

## Sources of Truth

| Owner                                                                       | Contract                                                                 |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| [Theme definitions](../../frontend/packages/a13n-ui/src/styles/theme.css)   | Semantic colors, light and dark themes, and Tailwind mappings            |
| [Application tokens](../../frontend/packages/a13n-ui/src/styles/tokens.css) | Application aliases, brand typography, and shared scrollbar tokens       |
| [Typed exports](../../frontend/packages/a13n-ui/src/index.ts)               | Public component signatures                                              |
| [Source provenance](../../frontend/packages/a13n-ui/coss-source.json)       | Imported registry revision, source paths, license, and local adaptations |
| [Console patterns](../../frontend/apps/a13n-console/src/shared/README.md)   | Console page, collection, form, and dialog compositions                  |
| This document                                                               | Observable visual and interaction rules and their ownership              |

`src/components`, `src/hooks`, and `src/lib` contain imported registry sources. `src/patterns` contains reusable field, modal, search, disclosure, settings, and status compositions; `src/brand` contains project identity assets and the shared product and provider brand registry. Applications use public components directly and keep business-specific compositions in their owning feature or shared directory. Recurring control behavior and styling are corrected in their shared owner rather than overridden per screen.

## Principles

1. **Surfaces, not outlines.** Grouping uses a soft 3–4% fill (`--a13n-surface`) on the canvas, or whitespace alone. Outlines are reserved for text inputs and selection triggers. Sections, cards, tables, rails, and dialogs never draw a border around themselves; nested borders and resting shadows do not accumulate.
2. **One anatomy per screen type.** Every list page, detail page, editor, dialog, and settings page follows the shared anatomy below. A reader who has used one resource can use all of them.
3. **Names before identifiers.** People recognize resources by name, icon, and status. Identifiers are secondary, sans-serif, muted, and copyable; they never lead a row or a title.
4. **The primary action is obvious, once.** Each page has at most one filled primary button, aligned with its heading. Everything else is outline, ghost, or lives in a menu.
5. **State is explicit.** Drafts, saving, saved, failed, disabled, and pending states are visible where the change happens, in words, not only in color.
6. **Progressive disclosure.** Common settings stay visible; advanced or rare configuration lives in a quiet disclosure with a closed-state summary. Nothing important is hidden behind a hover.
7. **Density with air.** Rows are compact, but the space between sections is always larger than the space inside them.

## Tokens and Surfaces

| Role         | Token                       | Use                                                                  |
| ------------ | --------------------------- | -------------------------------------------------------------------- |
| Canvas       | `--a13n-canvas`             | Page background                                                      |
| Surface      | `--a13n-surface` (3–4%)     | Grouping regions, list rows, rails, tiles, code blocks, empty states |
| Elevated     | `--a13n-elevated`           | Popovers, dialogs, icon tiles and controls placed on a surface       |
| Hover        | `--a13n-hover`              | Interactive row and tile hover                                       |
| Selected     | `--a13n-selected`           | Active navigation and selected list rows                             |
| Hairline     | `--a13n-border-soft` (6–8%) | Row dividers inside a table or settings group only                   |
| Input border | `--a13n-input-border`       | Input, Textarea, Select triggers                                     |
| Text         | `--a13n-text`               | Primary text                                                         |
| Secondary    | `--a13n-secondary`          | Descriptions, metadata, labels in rails                              |
| Tertiary     | `--a13n-tertiary`           | Counts and the quietest supporting text                              |
| Signal       | `--a13n-signal`             | Focus rings, text selection, and the first chart series              |
| Raised       | `--a13n-shadow-subtle`      | Icon tiles and controls on a surface                                 |
| Overlay      | `--a13n-shadow`             | Popovers, menus, selects, and tooltips                               |
| Floating     | `--a13n-shadow-floating`    | Dialogs and floating bars                                            |

Neutrals are cool rather than warm. Each elevation step is a 1px hairline ring plus a softer, larger shadow, so layers separate without borders. Radii: 12px for grouping surfaces, dialogs, and empty states; 10px for popovers and menus; 8px for rows, tiles, inputs, and buttons; 6px for chips; 999px for pills. Dark mode uses the same roles with inverted alpha fills and raises elevated surfaces slightly above the canvas; every surface must read correctly in both themes.

## Typography

Interface text uses the system sans-serif family at 13px; prose and message content use 14px with 1.6 line height. One scale serves every screen: 11, 12, 13, 14, 15, 17, and 24px (`--a13n-text-2xs` through `--a13n-text-2xl`), with no half-pixel sizes; hierarchy comes from these steps, weight, and color. Weights are 600 for page, dialog, and section titles, 500 for labels, names, and header cells, and 400 for everything else. Titles tighten their tracking slightly.

| Element                    | Size / weight | Color     |
| -------------------------- | ------------- | --------- |
| Page title                 | 24px / 600    | text      |
| Dialog title               | 17px / 600    | text      |
| Section title              | 15px / 600    | text      |
| Row name                   | 14px / 500    | text      |
| Field label                | 13px / 500    | text      |
| Body, table cells          | 13px / 400    | text      |
| Page and section summaries | 13px / 400    | secondary |
| Help text, metadata        | 12px / 400    | secondary |
| Table headers              | 12px / 500    | secondary |
| Rail and group headings    | 11px / 500    | secondary |
| Identifiers, keys, URLs    | 12px / 400    | secondary |

Monospace is reserved for code, JSON, commands, and Markdown code. Identifiers use the sans family; a copy affordance appears beside them. Numbers that are compared use tabular figures.

## Color and Status

Neutral surfaces carry the interface. The single filled accent is the primary button (near-black in light, near-white in dark). Indigo is a signal, not a brand fill: it marks focus, text selection, and the first chart series, and never fills buttons or decorates. Links inside the interface use text color with medium weight and an underline on hover; external links carry a trailing arrow icon and open in a new tab only when leaving the product.

Status is a 6px dot and a 12px medium label. Settled states (success and neutral) stay untinted, so a column of healthy rows reads calm; states that ask for attention (warning, danger, and info) add a 10–12% tint of the same hue. Semantic hues are success (emerald), warning (amber), danger (red), info (blue), and neutral (gray). Color never carries meaning alone; the label always names the state.

## Page Anatomy

**List page.** Back link (only when nested) → title row (24px title, optional count) with the primary action at the right → one-line description → toolbar (search up to 300px with leading icon, narrowing before the filters wrap, then filter chips, then secondary actions at the right) → collection → footer (result count at left, pagination at right). Every collection has the toolbar, so moving between lists never shifts the collection. Content spans up to 1280px with 36px top and 48px side padding, and the page reserves the scrollbar gutter so the content width is the same whether or not a page scrolls.

**Detail page.** Back link → identity header (44px avatar or brand tile, 24px name, status pill, edit affordance, key chip with copy, one-line description) with primary actions at the right → underline tabs → content up to 1180px, optionally with a 256px sticky summary rail at the right. Sections are titled at 15px with a 13px description and separated by 36px; a section that lists items uses surface rows; a section that edits values uses fields.

**Editor with drafts.** Edits accumulate in a draft. A floating save bar appears at the bottom center with a pulse dot, "Unsaved changes", a one-line consequence, an optional note field, Discard, and the primary Save. Autosaved settings show an inline status beside the control instead.

**Settings page.** A contextual layout with its own left navigation (Back to workspace, search, grouped entries). Content is one column up to 1040px whose forms and groups stop at 680px while collections use the full width, so the left edge never moves between sections: 24px title, then groups on a surface with rows (name and explanation at the left, control at the right, hairline between rows). Groups are separated by whitespace and titled outside the surface.

**Dialog.** 520px for standard forms and 640px for complex ones, vertically stable across steps. Header: 17px title, 13px description. Creation flows start with a catalog (tiles or a searchable list) and continue with a step whose title carries the chosen brand. Fields use 16px gaps; advanced groups use a disclosure; the footer is sticky with Cancel and the primary action. Destructive confirmations name the resource and label the action with its verb.

**Provider editors.** Adding a provider is a catalog step followed by a connect step: the credential first with a link to where it is issued, the required configuration, the name, and optional settings behind an Advanced settings disclosure. Editing a provider is one calm dialog: a brand-marked title with the definition name and scope as the description, the name field, one soft settings group (Enabled, the credential row, read-only connection facts), the optional disclosure, and a footer whose leading slot holds Check connection beside Cancel and the primary action. The credential row states what is stored (Saved, Not configured) and only reveals inputs on Replace or Add; backing out reads as keeping the saved key. No section headings, no provider type row, no hairlines between blocks.

**Empty state.** Centered on a surface: a 40px icon tile, 15px title, 13px explanation, and the primary action. When the empty state offers that action, the page header does not repeat it; the screen still offers it exactly once. Search results with no matches use a shorter inline message and keep the toolbar.

**Loading.** Skeletons preserve the destination's layout and density; spinners are reserved for gates and explicit actions. Existing content stays visible during background refreshes.

## Collections

One `ResourceTable` serves every resource list. The list is part of the page: the page scrolls, the table never scrolls inside a fixed-height box. Header cells are 40px tall with 12px medium sentence-case secondary text and no fill. Rows are 52–60px with a hairline divider, a 3–4% hover fill, and a pointer cursor when the row opens something. The first column is the identity: a 32px icon tile or avatar, the name at 14px medium, and one line of secondary text (key, source, or summary). Supporting columns use 13px text; timestamps and identifiers use 12px secondary text; status uses a pill. Columns that are compared as numbers align right with tabular figures; timestamps stay left with their own inset. A search field carries its accessible name, and a placeholder when the field accepts something more specific than the name says. Row actions live in an overflow menu at the right that stays visible on touch devices. Long lists load in pages with a footer count and Previous/Next; search and filters query the server, or read the whole collection and match in the browser when the Service cannot filter it, never only the current page.

Selection lists inside pickers use the same row anatomy with a checkbox in place of the tile, sorted with selected items first.

## Forms and Fields

Labels sit above controls at 13px medium; descriptions sit below the control at 12px secondary; validation replaces the description in the danger color and names the fix. Optional fields say "(optional)" in the label; required fields carry no asterisk. Inputs are 32px tall (36px below the `sm` breakpoint) with an 8px radius and a hairline border. Controls that share a row share a height: page header and toolbar controls are 32px, section actions and footer controls 28px, and extra-small icon buttons 24px. Textareas for long content (instructions, prompts, JSON) use a soft surface with no border, grow to a bounded height, and scroll internally. Selects and pickers show the current value with one trailing chevron. Read-only values use `ReadOnlyField`; long-form text retains the soft surface and padding, while short values have no box.

Buttons: primary (filled), outline (secondary), ghost (tertiary and inline), and destructive (only inside confirmations and menus). Icon-only buttons always have a tooltip and an accessible name. Loading preserves size and disables activation. Menu items pair an icon with a label; destructive items sit last after a separator. A form whose submit is not the screen's primary action uses the outline button for it, so the one filled primary per screen still holds.

A segmented control switches an in-place view or mode: a track on the soft surface, the active segment lifted onto the elevated surface, medium labels, and no outline. The small control is 28px tall with 12px labels, matching section actions; the default is 32px with 13px labels, matching toolbars. It never navigates and never replaces tabs; an unavailable segment is disabled rather than hidden. Fields generated from a schema are named by the property that holds them; a schema's own type name is never a field label.

## Conversation Rendering

The session view keeps a compact header (agent identity, status pill, view switch, and actions, with the session identifier copied from the actions menu) and a centered 760px transcript. In Chat, the user's message is a right-aligned soft bubble and the agent's answer is full-width prose with a small avatar and name line, preceded by one quiet line that summarizes the tool calls and opens into compact rows. In Debug, each run is a section: a 13.5px medium heading with a status pill and 12px metadata at the right, the request beneath it, and a timeline with a 22px marker column carrying one glyph per entry kind and rows of one line each: the name at 13px medium, monospace only when it is a tool's own name, the subject as secondary prose, and right-aligned tabular numbers with a small duration bar. Chat's compact rows carry the same names, glyphs and subjects at the same level of detail. Model requests are the skeleton; the entries they emitted indent one level beneath them. Expanding a row opens a bounded pane on the 4% surface with 11px medium section labels, code blocks on the elevated surface, patches with additions and removals tinted success and danger, and a folded request summary; raw payloads open in a named dialog. Sections are separated by more space than the rows inside them, and nested content never accumulates borders. Failures are a quiet danger-tinted notice with a title, a plain-language explanation, and an "Error details" disclosure. The composer floats at the bottom as an elevated surface: a borderless text area, attachment and option chips at the left, and a round primary send button at the right; Enter sends and Shift+Enter inserts a newline.

## Documentation Pages

The documentation site is a reading surface, so its pages set type for sustained reading rather than interface density. Prose is 16px with a 1.7 line height in supporting text (`--a13n-text-supporting`), so headings, bold text, links, and code stand out in text color. Page titles are 30px, section headings 22px, and subsection headings 18px, all at weight 600, which prose also uses for bold text; a hairline above each section heading divides the page. The description beneath the title is 18px secondary text. The reading column is at most 800px wide, centered between the navigation and the table of contents. The navigation sits on the canvas without a tint: rows keep the 32px height and 8px radius but use text color, and group labels are 13px at weight 600 in text color. The copy and open page actions follow the table of contents when it is shown and otherwise sit beside the title.

Hue appears in page content only where it carries meaning. Links keep the text color at weight 500 over a signal underline that strengthens on hover. Inline code in running text is a chip on the surface with a 6px radius and an input-border outline; in headings and controls it is bare monospace. Table headers sit on a rounded surface band in 12px secondary text at weight 600. Code blocks have a hairline frame on the surface and a title bar naming the file, or the language when no file is given; plain text and output keep a bare frame. Code tokens use one vivid palette in both themes: keywords pink, functions and commands violet, types teal, keys red, strings green, and literals orange, with comments and punctuation gray. Callouts are surfaces tinted with their kind's hue: note info, tip success, important violet, warning amber, and caution red.

Diagrams sit on a surface panel with a dot grid that fades toward its edges. Nodes, states, and sequence participants are cards with a hairline ring and a soft shadow; groups and sequence frames are hairline outlines with their titles at the top-left, edges are thin with small arrowheads, and edge labels are masked by the panel. Wrapped labels break into lines of even length. A diagram source may mark node categories with `class Node <category>`: `a13n` (violet) for Agent Foundation components, `app` (teal) for people and calling applications, `store` (orange) for state and storage, `ext` (pink) for external models, services, and tools, and `success`, `warning`, and `danger` for outcomes. A category tints the card, its ring, and its label. Sequence diagrams have no class statement, so they write the same line as a comment, `%% class Node <category>`; the participant's lifeline, its messages, and the numbered step at the start of each message take its color. GitHub renders the same source without these colors, so a diagram never depends on them.

## Navigation

The sidebar is a tinted column without a border: workspace switcher at the top, navigation rows at 32px with an 8px radius, active rows on the selected fill with a duotone icon, 11px medium sentence-case group labels, and the account menu at the bottom. Hover is lighter than the active fill, so pointing at a row never reads as being on it. Selection changes only the fill and the icon, never the text weight, here and in every selectable list, so labels never shift; an expanded group highlights its selected child rather than itself. It collapses to an icon rail with tooltips. Below the shared `md` breakpoint (768px), navigation opens in a drawer from an always-visible menu button whose icon aligns with the page content gutter while retaining its full click target. Named JavaScript media queries match Tailwind's breakpoints and use exclusive upper bounds so resizing never leaves a gap between mobile and desktop navigation. Settings open in the contextual layout with a clear way back. Internal links never open new tabs. Every route names itself in the document title, settings sections included.

## Overlays and Feedback

Popovers and menus use the elevated surface, a 10px radius, and the overlay elevation; dialogs use the floating elevation; neither draws a border. Toasts appear at the top center for isolated action results. Inline error notices stay beside the form or collection that owns the failure and offer a recovery action when one exists. Full error pages are reserved for views that cannot function.

## Scrolling and Long Content

`ScrollArea` and the `a13n-scrollbar` utility share size, thumb, and hover tokens. Textareas bound their own height; dialogs scroll their body under a fixed header and sticky footer; bounded lists scroll inside their surface. Nothing important hides behind a fixed control.

## Accessibility and Motion

Every interactive element keeps a visible focus ring at both densities. Titled regions name themselves through their heading, so assistive technology can address them. Fields and icon-only actions have accessible names; tooltips complement, never replace, them. A resource without an image presents initials from the first and last word of its name on a hue derived from its identifier, so similar names stay distinguishable. Base UI controls keep keyboard interaction and disabled semantics. Motion is short (100–200ms), used for hover fills, disclosures, and overlays, and respects reduced-motion preferences.

## Themes and Language

Applications load the shared stylesheet once and opt into base typography with `a13n-root` on the document body. Light is the default theme; the document element toggles the `dark` class so portaled content inherits tokens. Console uses English as the default and fallback and supports Simplified Chinese; shared components receive application-owned strings.

## Development Showcase and Validation

The showcase in `a13n-ui/dev` demonstrates foundations, primitive states, settings, and collection compositions through its own Vite entry. Package checks type-check sources, exercise interaction contracts, and build the showcase. Application builds verify consumption through workspace exports. Visual changes are verified on the rendered page in both themes and at a narrow width before handoff.
