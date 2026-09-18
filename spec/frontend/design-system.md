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

1. **Surfaces, not outlines.** Grouping uses a soft 4% fill (`--a13n-surface`) on the canvas, or whitespace alone. Outlines are reserved for text inputs and selection triggers. Sections, cards, tables, rails, and dialogs never draw a border around themselves; nested borders and resting shadows do not accumulate.
2. **One anatomy per screen type.** Every list page, detail page, editor, dialog, and settings page follows the shared anatomy below. A reader who has used one resource can use all of them.
3. **Names before identifiers.** People recognize resources by name, icon, and status. Identifiers are secondary, sans-serif, muted, and copyable; they never lead a row or a title.
4. **The primary action is obvious, once.** Each page has at most one filled primary button, aligned with its heading. Everything else is outline, ghost, or lives in a menu.
5. **State is explicit.** Drafts, saving, saved, failed, disabled, and pending states are visible where the change happens, in words, not only in color.
6. **Progressive disclosure.** Common settings stay visible; advanced or rare configuration lives in a quiet disclosure with a closed-state summary. Nothing important is hidden behind a hover.
7. **Density with air.** Rows are compact, but the space between sections is always larger than the space inside them.

## Tokens and Surfaces

| Role          | Token                       | Use                                                                  |
| ------------- | --------------------------- | -------------------------------------------------------------------- |
| Canvas        | `--a13n-canvas`             | Page background                                                      |
| Surface       | `--a13n-surface` (4% fill)  | Grouping regions, list rows, rails, tiles, code blocks, empty states |
| Elevated      | `--a13n-elevated`           | Popovers, dialogs, icon tiles and controls placed on a surface       |
| Hover         | `--a13n-hover`              | Interactive row and tile hover                                       |
| Selected      | `--a13n-selected`           | Active navigation and selected list rows                             |
| Hairline      | `--a13n-border-soft` (6–8%) | Row dividers inside a table or settings group only                   |
| Input border  | `--a13n-input-border`       | Input, Textarea, Select triggers                                     |
| Text          | `--a13n-text`               | Primary text                                                         |
| Secondary     | `--a13n-secondary`          | Descriptions, metadata, labels in rails                              |
| Shadow        | `--a13n-shadow`             | Popovers, floating bars, dialogs                                     |
| Subtle shadow | `--a13n-shadow-subtle`      | Icon tiles on a surface                                              |

Radii: 12px for grouping surfaces, dialogs, and empty states; 10px for rows, tiles, and inputs; 8px for icon tiles and chips; 999px for pills. Dark mode uses the same roles with inverted alpha fills; every surface must read correctly in both themes.

## Typography

Interface text uses the system sans-serif family at 13px; prose and message content use 14px with 1.6 line height. Weights are 500 for titles, labels, and names, and 400 for everything else. Bold (600+) is not used in interface chrome.

| Element                 | Size / weight   | Color     |
| ----------------------- | --------------- | --------- |
| Page title              | 22px / 500      | text      |
| Dialog title            | 18px / 500      | text      |
| Section title           | 15px / 500      | text      |
| Row name, field label   | 13.5px / 500    | text      |
| Body, table cells       | 13px / 400      | text      |
| Descriptions, help text | 12.5px / 400    | secondary |
| Metadata, timestamps    | 12px / 400      | secondary |
| Rail and group headings | 11px / 500 caps | secondary |
| Identifiers, keys, URLs | 12px / 400      | secondary |

Monospace is reserved for code, JSON, commands, and Markdown code. Identifiers use the sans family; a copy affordance appears beside them. Numbers that are compared use tabular figures.

## Color and Status

Neutral surfaces carry the interface. The single accent is the primary button (near-black in light, near-white in dark). Links inside the interface use text color with medium weight and an underline on hover; external links carry a trailing arrow icon and open in a new tab only when leaving the product.

Status is a pill: a 6px dot, 12px medium text, and a 10% tinted background of the same hue. Semantic hues are success (emerald), warning (amber), danger (red), info (blue), and neutral (gray). Successful and idle states stay quieter than failures and waiting states. Color never carries meaning alone; the label always names the state.

## Page Anatomy

**List page.** Back link (only when nested) → title row (22px title, optional count) with the primary action at the right → one-line description → toolbar (search 300px with leading icon, then filter chips, then secondary actions at the right) → collection → footer (result count at left, pagination at right). Content spans up to 1280px with 28px top and 40px side padding.

**Detail page.** Back link → identity header (44px avatar or brand tile, 22px name, status pill, edit affordance, key chip with copy, one-line description) with primary actions at the right → underline tabs → content up to 1180px, optionally with a 256px sticky summary rail at the right. Sections are titled at 15px with a 12.5px description and separated by 36px; a section that lists items uses surface rows; a section that edits values uses fields.

**Editor with drafts.** Edits accumulate in a draft. A floating save bar appears at the bottom center with a pulse dot, "Unsaved changes", a one-line consequence, an optional note field, Discard, and the primary Save. Autosaved settings show an inline status beside the control instead.

**Settings page.** A contextual layout with its own left navigation (Back to workspace, search, grouped entries). Content is a single 760px column: 22px title, then groups on a surface with rows (name and explanation at the left, control at the right, hairline between rows). Groups are separated by whitespace and titled outside the surface.

**Dialog.** 520px for standard forms and 640px for complex ones, vertically stable across steps. Header: 18px title, 13px description. Creation flows start with a catalog (tiles or a searchable list) and continue with a step whose title carries the chosen brand. Fields use 16px gaps; advanced groups use a disclosure; the footer is sticky with Cancel and the primary action. Destructive confirmations name the resource and label the action with its verb.

**Empty state.** Centered on a surface: a 40px icon tile, 15px title, 13px explanation, and the primary action. When the empty state offers that action, the page header does not repeat it; the screen still offers it exactly once. Search results with no matches use a shorter inline message and keep the toolbar.

**Loading.** Skeletons preserve the destination's layout and density; spinners are reserved for gates and explicit actions. Existing content stays visible during background refreshes.

## Collections

One `ResourceTable` serves every resource list. The list is part of the page: the page scrolls, the table never scrolls inside a fixed-height box. Header cells are 11px medium uppercase secondary text without a fill. Rows are 52–60px with a hairline divider, a 3–4% hover fill, and a pointer cursor when the row opens something. The first column is the identity: a 32px icon tile or avatar, the name at 13.5px medium, and one line of secondary text (key, source, or summary). Supporting columns use 13px text; timestamps and identifiers use 12px secondary text; status uses a pill. Columns that are compared as numbers align right with tabular figures; timestamps stay left with their own inset. A search field carries its accessible name, and a placeholder when the field accepts something more specific than the name says. Row actions live in an overflow menu at the right that stays visible on touch devices. Long lists load in pages with a footer count and Previous/Next; search and filters query the server, never only the current page.

Selection lists inside pickers use the same row anatomy with a checkbox in place of the tile, sorted with selected items first.

## Forms and Fields

Labels sit above controls at 13.5px medium; descriptions sit below the control at 12.5px secondary; validation replaces the description in the danger color and names the fix. Optional fields say "(optional)" in the label; required fields carry no asterisk. Inputs are 36px tall with a 10px radius and a hairline border; compact inputs in toolbars are 32px. Textareas for long content (instructions, prompts, JSON) use a soft surface with no border, grow to a bounded height, and scroll internally. Selects and pickers show the current value with one trailing chevron. Read-only values use `ReadOnlyField` with no box.

Buttons: primary (filled), outline (secondary), ghost (tertiary and inline), and destructive (only inside confirmations and menus). Icon-only buttons always have a tooltip and an accessible name. Loading preserves size and disables activation. Menu items pair an icon with a label; destructive items sit last after a separator. A form whose submit is not the screen's primary action uses the outline button for it, so the one filled primary per screen still holds.

A segmented control switches an in-place view or mode: a track on the soft surface, the active segment lifted onto the elevated surface, 12px medium labels, and no outline. It never navigates and never replaces tabs; an unavailable segment is disabled rather than hidden. Fields generated from a schema are named by the property that holds them; a schema's own type name is never a field label.

## Conversation Rendering

The session view keeps a compact header (title, agent chip, status pill, actions) and a centered 760px transcript. The user's message is a right-aligned soft bubble; the agent's answer is full-width prose with a small avatar and name line. Tool calls, reasoning, and other execution details render as compact rows in one surface group: an icon tile, the tool name, a one-line summary, and the status or duration at the right; expanding a row reveals arguments and results as bounded code blocks. Failures are a quiet danger-tinted notice with a title, a plain-language explanation, and an "Error details" disclosure. The composer floats at the bottom as an elevated surface: a borderless text area, attachment and option chips at the left, and a round primary send button at the right; Enter sends and Shift+Enter inserts a newline. The inspector is a right panel with 12px labels and 13px values grouped under 11px headings; raw payloads stay in disclosures.

## Navigation

The sidebar is a tinted column without a border: workspace switcher at the top, navigation rows at 32px with an 8px radius, active rows on the selected fill with a duotone icon, 11px uppercase group labels, and the account menu at the bottom. Hover is lighter than the active fill, so pointing at a row never reads as being on it. It collapses to an icon rail with tooltips. Settings open in the contextual layout with a clear way back. Internal links never open new tabs. Every route names itself in the document title, settings sections included.

## Overlays and Feedback

Popovers and menus use the elevated surface, an 10px radius, and the shared shadow; no border. Toasts appear at the top center for isolated action results. Inline error notices stay beside the form or collection that owns the failure and offer a recovery action when one exists. Full error pages are reserved for views that cannot function.

## Scrolling and Long Content

`ScrollArea` and the `a13n-scrollbar` utility share size, thumb, and hover tokens. Textareas bound their own height; dialogs scroll their body under a fixed header and sticky footer; bounded lists scroll inside their surface. Nothing important hides behind a fixed control.

## Accessibility and Motion

Every interactive element keeps a visible focus ring at both densities. Titled regions name themselves through their heading, so assistive technology can address them. Fields and icon-only actions have accessible names; tooltips complement, never replace, them. A resource without an image presents initials from the first and last word of its name on a hue derived from its identifier, so similar names stay distinguishable. Base UI controls keep keyboard interaction and disabled semantics. Motion is short (100–200ms), used for hover fills, disclosures, and overlays, and respects reduced-motion preferences.

## Themes and Language

Applications load the shared stylesheet once and opt into base typography with `a13n-root` on the document body. Light is the default theme; the document element toggles the `dark` class so portaled content inherits tokens. Console uses English as the default and fallback and supports Simplified Chinese; shared components receive application-owned strings.

## Development Showcase and Validation

The showcase in `a13n-ui/dev` demonstrates foundations, primitive states, settings, and collection compositions through its own Vite entry. Package checks type-check sources, exercise interaction contracts, and build the showcase. Application builds verify consumption through workspace exports. Visual changes are verified on the rendered page in both themes and at a narrow width before handoff.
