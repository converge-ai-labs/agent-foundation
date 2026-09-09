# Frontend Design System

## Design Position

`frontend/packages/a13n-ui` provides shared React primitives and design tokens for browser applications. Radix owns focus, overlays, selection, and menu semantics; the unstyled cmdk library owns searchable command navigation; CSS Modules and namespaced CSS custom properties own presentation. The system uses restrained surfaces, clear typography, readable secondary text, consistent spacing, and thin visible focus indicators.

The [repository model](../repository-model.md#frontend-workspace) owns workspace and packaging boundaries. Product layouts, navigation, conversation rendering, protocol state, data fetching, and translations belong to applications. Shared components do not import applications or interpret domain states.

## Sources of Truth

[Token definitions](../../frontend/packages/a13n-ui/src/styles/tokens.css) own concrete color, typography, spacing, sizing, radius, focus, motion, and layering values. [Typed exports](../../frontend/packages/a13n-ui/src/index.ts) own component signatures. This document defines behavior and ownership without duplicating their value tables.

The [public exports](../../frontend/packages/a13n-ui/src/index.ts) include form controls, searchable pickers, action menus, a command palette, settings compositions, tabs, and feedback primitives. SearchInput provides a standalone, accessibly named search field with a visible border and focus treatment, without a separate visible form label. Badge supports a plain status presentation with a dot and text as well as its default label surface. Badge tones describe generic visual meaning; applications map domain states to those tones and translated labels. Select options accept a text label and an optional decorative React icon.

## Brand Typography

The package owns the bundled Space Grotesk Bold typeface, its license, and the `--a13n-font-brand` token. `Wordmark` renders the a13n name with shared weight and letter spacing; applications choose its size and placement. Use it consistently in login screens, sidebar brands, and other a13n brand surfaces. Pair it with a decorative `Logo` when the name already supplies the accessible text. Product body text retains the standard interface font.

## Visual Hierarchy and Composition

Separate the application frame, softly tinted content canvas, and elevated surfaces. Settings group related controls on quiet elevated surfaces with subtle borders. Use additional grouping in complex forms only when it clarifies distinct sections. Section headings and explanations sit outside their groups; compact scalar controls align beside explanatory copy while long content occupies the group width. Neutral selection distinguishes navigation and views; accent colors emphasize primary actions and active controls. Borders and shadows separate surfaces without competing with content. Shared control defaults have lower specificity than component styles so import order cannot override component appearance.

Standard controls serve forms; compact controls serve toolbars, settings rows, and properties. Ghost controls expose inline editable values without enclosing every property in a field. Keep text readable at both densities and provide visible keyboard focus. Settings sections provide grouped surfaces or plain sections separated by rules, selected by the caller. Both presentations group related rows; each row pairs its name and optional explanation with a control. Narrow containers stack labels above controls without truncating names or explanations. Disabled explanations remain readable.

Navigation uses the shared compact row-height token, small gaps within groups, and modest separation between groups. Page headings stay proportional to interface text; primary actions align with the heading, while search and filters sit together above the content. Keep the gap before a section and between sections larger than the spacing within a form or row; compact controls do not imply compressing the separation between content groups. Avoid repeated explanatory copy, decorative labels, oversized scalar fields, and nested surfaces that add no hierarchy. Tables use compact rows and align action-column headings with their controls.

## Interaction and Accessibility

Buttons preserve native button semantics and default to a non-submitting button. Disabled or loading buttons do not activate. Idle text buttons have symmetric padding and no empty icon slot. An icon slot appears only for an icon or active loading indicator; normal and loading labels participate in sizing without duplicating child content. Loading exposes busy state; Spinner itself is decorative.

Select is a standalone control with an accessible name, compact and standard sizes, and default or ghost presentation. SelectField composes it with a visible label, hint, and error. Use SelectField when migrating callers that previously used the labeled Select API. Fields associate visible labels, hints, and validation messages with their controls. Errors expose invalid state. Radix selection and toggle controls retain keyboard interaction and disabled semantics. Dialog provides a title, description, focus containment, Escape dismissal, and focus restoration. Its title and close control remain visible while the content scrolls within the rounded surface; an optional footer stays outside that scroll area. A form-owned action footer marked `data-a13n-form-actions` remains sticky within the scroll area, retaining native form submission and leaving the last fields reachable by scrolling. Complex forms use the wide size, with viewport-bounded dimensions at both sizes. Tooltip complements an already named trigger and does not provide the only accessible name.

Picker searches option labels, descriptions, group names, and caller-provided aliases. Values are unique across groups; group names identify groups. Search is local to the opened picker and resets when it closes. Current selection appears on the trigger and with a check mark in the result list. Disabled options cannot activate, empty results have an explicit message, and Escape dismisses the innermost overlay first. Menus support nested action groups and checked current items with Radix submenu keyboard behavior; pickers choose values using searchable list semantics. Workspace navigation uses a compact menu; search belongs in pickers for longer selectable resource lists. Searchable composites own their focus treatment on the surrounding search row, without a second outline on the inner text input.

CommandPalette reuses searchable list presentation inside a named modal. Opening focuses the search field; selecting an action closes the palette; closing restores focus. Applications own command execution, shortcut registration, and whether shortcuts should run while editing or while another dialog is open. Kbd is a visual hint and registers no listener. Tabs provide arrow-key navigation and expose only the active panel.

SettingsRow can associate a visible label with a control through controlId; its description uses `<controlId>-description`. Callers connect that description with aria-describedby. A Switch can hide its own visual label when the surrounding row supplies it, retaining its accessible name.

Components retain visible focus indicators, and motion respects reduced-motion preferences. Callers supply accessible names for icon-only actions and all user-facing strings.

## Themes and Language

Applications load the shared stylesheet once and opt into base typography with `a13n-root`. Light is the default theme. The document element owns `data-a13n-theme`, ensuring portaled Select, Dialog, and Tooltip content inherits the same tokens. Per-subtree mixed themes are outside the supported contract.

English is the application default and fallback; Simplified Chinese is supported. Applications own language selection and persistence. Console stores a supported language locally; unavailable browser storage leaves the selection usable for the current session. Shared components have no translation runtime or locale persistence.

## Development Showcase

The showcase lives in `a13n-ui/dev` and runs through its own Vite entry. It includes foundations, primitive states, settings, and collection/detail compositions; navigation and filter data remain local demonstration state. It imports the public shared components and offers theme, language, and narrow-width previews, token specimens, and interactive control states. It is neither a Console route nor a public package export. Production applications do not import showcase code or styles.

Package checks type-check sources, exercise interaction contracts, and build the showcase. Application builds verify consumption through workspace exports. Browser checks cover focus, nested overlays, loading dimensions, theme inheritance, translated labels, and responsive layouts including narrow viewports.
