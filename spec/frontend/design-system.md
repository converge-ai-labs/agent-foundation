# Frontend Design System

## Design Position

`frontend/packages/a13n-ui` provides shared React primitives and design tokens for browser applications. Coss UI registry components provide the shared visual and interaction foundation, using Base UI for focus, overlays, selection, and menu semantics and DayPicker for calendars. Tailwind CSS owns component styling and semantic theme utilities; CSS Modules remain available for application-specific layouts. The system uses restrained surfaces, clear typography, readable secondary text, consistent spacing, and visible focus indicators.

The [repository model](../repository-model.md#frontend-workspace) owns workspace and packaging boundaries. Product layouts, navigation, conversation rendering, protocol state, data fetching, and translations belong to applications. Shared components do not import applications or interpret domain states.

## Sources of Truth

[Theme definitions](../../frontend/packages/a13n-ui/src/styles/theme.css) own shared semantic colors and Tailwind theme mappings. [Application tokens](../../frontend/packages/a13n-ui/src/styles/tokens.css) connect application layouts and brand typography to that theme. [Typed exports](../../frontend/packages/a13n-ui/src/index.ts) own component signatures. This document defines behavior and ownership without duplicating their value tables.

The [public exports](../../frontend/packages/a13n-ui/src/index.ts) expose Coss UI primitives and small reusable compositions. `src/components`, `src/hooks`, and `src/lib` contain imported registry sources; [source provenance](../../frontend/packages/a13n-ui/coss-source.json) records the upstream revision, source paths, license, and local adaptations. Only the MIT-licensed UI registry is included. `src/patterns` contains shared field, modal, search, disclosure, and settings compositions; `src/brand` contains project identity assets. Applications use these components directly and keep business-specific compositions in their owning feature or shared directory.

Search fields retain a visible border and an accessible name. Badge variants describe visual meaning; applications map domain states to variants and translated labels. Choice options accept text labels and optional decorative icons.

## Brand Typography

The package owns the bundled Space Grotesk Bold typeface, its license, and the `--a13n-font-brand` token. `Wordmark` renders the a13n name with shared weight and letter spacing; applications choose its size and placement. Use it consistently in login screens, sidebar brands, and other a13n brand surfaces. Pair it with a decorative `Logo` when the name already supplies the accessible text. Product body text retains the standard interface font.

## Visual Hierarchy and Composition

Separate the application frame, softly tinted content canvas, and elevated surfaces. Settings group related controls on quiet elevated surfaces with subtle borders. Use additional grouping in complex forms only when it clarifies distinct sections. Section headings and explanations sit outside their groups; compact scalar controls align beside explanatory copy while long content occupies the group width. Neutral selection distinguishes navigation and views; accent colors emphasize primary actions and active controls. Borders and shadows separate surfaces without competing with content. Shared control defaults have lower specificity than component styles so import order cannot override component appearance.

Standard controls serve forms; compact controls serve toolbars, settings rows, and properties. Ghost controls expose inline editable values without enclosing every property in a field. Keep text readable at both densities and provide visible keyboard focus. Settings sections provide grouped surfaces or plain sections separated by rules, selected by the caller. Both presentations group related rows; each row pairs its name and optional explanation with a control. Narrow containers stack labels above controls without truncating names or explanations. Disabled explanations remain readable.

Navigation uses compact Coss UI Sidebar rows, small gaps within groups, and modest separation between groups. Page headings stay proportional to interface text; primary actions align with the heading, while search and filters sit together above the content. Keep the gap before a section and between sections larger than the spacing within a form or row; compact controls do not imply compressing the separation between content groups. Avoid repeated explanatory copy, decorative labels, oversized scalar fields, and nested surfaces that add no hierarchy. Tables use compact rows and align action-column headings with their controls.

## Interaction and Accessibility

Buttons preserve native button semantics and default to a non-submitting button. Disabled or loading buttons do not activate. Idle text buttons have symmetric padding and no empty icon slot. Loading preserves button content and dimensions, prevents activation, and exposes busy state. Its overlaid spinner is decorative; standalone loading indicators receive translated status text.

Select exposes Coss UI composition parts; ChoiceField combines them with a label, description, and error. FormField provides the same field semantics for Input and Textarea. Fields associate labels, descriptions, and validation messages with controls and expose invalid state. Base UI controls retain keyboard interaction and disabled semantics. ModalFrame composes Dialog parts for resource forms. Dialog provides a title, description, focus containment, Escape dismissal, and focus restoration. Its title and close control remain visible while the content scrolls within the rounded surface; an optional footer stays outside that scroll area. A form-owned action footer marked `data-a13n-form-actions` remains sticky within the scroll area, retaining native form submission and leaving the last fields reachable by scrolling. Complex forms use the wide size, with viewport-bounded dimensions at both sizes. Tooltip complements an already named trigger and does not provide the only accessible name.

SearchPicker searches option labels, descriptions, group names, and caller-provided aliases. Values are unique across groups; group names identify groups. Search is local to the opened picker and resets when it closes. Current selection appears on the trigger and with a check mark in the result list. Disabled options cannot activate, empty results have an explicit message, and Escape dismisses the innermost overlay first. Menus support nested action groups and checked current items with Base UI submenu keyboard behavior; pickers choose values using searchable list semantics. Workspace navigation uses a compact menu; search belongs in pickers for longer selectable resource lists. Searchable composites own their focus treatment on the surrounding search row, without a second outline on the inner text input.

Tabs support arrow-key focus and keyboard activation; inactive panels are unavailable after their exit transition.

Calendar, Popover, and ChoiceField provide the primitives for date and time entry. Console's DateTimeField owns its translated labels, active locale, draft until Apply, clearing, dismissal behavior, and validation of local date-time strings. Applications own API timestamp conversion. Nonexistent local times are rejected.

SettingsRow can associate a visible label with a control through controlId; its description uses `<controlId>-description`. Callers connect that description with aria-describedby. A Switch uses the associated row label as its accessible name.

Components retain visible focus indicators, and motion respects reduced-motion preferences. Callers supply accessible names for icon-only actions and all user-facing strings.

## Themes and Language

Applications load the shared stylesheet once and opt into base typography with `a13n-root`. Light is the default theme. The document element toggles the `dark` class, ensuring portaled Select, Dialog, and Tooltip content inherits the same tokens. Per-subtree mixed themes are outside the supported contract.

English is the application default and fallback; Simplified Chinese is supported. Applications own language selection and persistence. Console stores a supported language locally; unavailable browser storage leaves the selection usable for the current session. Shared components have no translation runtime or locale persistence.

## Development Showcase

The showcase lives in `a13n-ui/dev` and runs through its own Vite entry. It includes foundations, primitive states, settings, and collection compositions; navigation and filter data remain local demonstration state. It imports the public shared components and offers theme and language controls with responsive layouts, token specimens, and interactive control states. It is neither a Console route nor a public package export. Production applications do not import showcase code or styles.

Package checks type-check sources, exercise interaction contracts, and build the showcase. Application builds verify consumption through workspace exports. Browser checks cover focus, nested overlays, loading dimensions, theme inheritance, translated labels, and responsive layouts including narrow viewports.
