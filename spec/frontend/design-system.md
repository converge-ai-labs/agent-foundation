# Frontend Design System

## Design Position

`frontend/packages/a13n-ui` provides shared React primitives and design tokens for browser applications. Radix owns focus, overlays, selection, and menu semantics; the unstyled cmdk library owns searchable command navigation; CSS Modules and namespaced CSS custom properties own presentation. The system uses restrained surfaces, clear typography, readable secondary text, consistent spacing, and thin visible focus indicators.

The [repository model](../repository-model.md#frontend-workspace) owns workspace and packaging boundaries. Product layouts, navigation, conversation rendering, protocol state, data fetching, and translations belong to applications. Shared components do not import applications or interpret domain states.

## Sources of Truth

[Token definitions](../../frontend/packages/a13n-ui/src/styles/tokens.css) own concrete color, typography, spacing, sizing, radius, focus, motion, and layering values. [Typed exports](../../frontend/packages/a13n-ui/src/index.ts) own component signatures. This document defines behavior and ownership without duplicating their value tables.

The [public exports](../../frontend/packages/a13n-ui/src/index.ts) include form controls, searchable pickers, action menus, a command palette, settings compositions, tabs, and feedback primitives. Badge tones describe generic visual meaning; applications map domain states to those tones and translated labels. Select options accept a text label and an optional decorative React icon.

## Visual Hierarchy and Composition

Separate the application frame, content canvas, and elevated surfaces. Neutral selection distinguishes navigation and views; accent colors emphasize primary actions and active controls. Borders and shadows separate surfaces without competing with content. Shared control defaults have lower specificity than component styles so import order cannot override component appearance.

Standard controls serve forms; compact controls serve toolbars, settings rows, and properties. Ghost controls expose inline editable values without enclosing every property in a field. Keep text readable at both densities and provide visible keyboard focus. Settings sections provide grouped surfaces or plain sections separated by rules, selected by the caller. Both presentations group related rows; each row pairs its name and optional explanation with a control. Narrow containers wrap controls without truncating their names or explanations. Disabled explanations remain readable.

## Interaction and Accessibility

Buttons preserve native button semantics and default to a non-submitting button. Disabled or loading buttons do not activate. Callers pass a stable boolean loading prop to reserve the icon slot; normal and loading labels participate in sizing without duplicating child content. Loading exposes busy state; Spinner itself is decorative.

Select is a standalone control with an accessible name, compact and standard sizes, and default or ghost presentation. SelectField composes it with a visible label, hint, and error. Use SelectField when migrating callers that previously used the labeled Select API. Fields associate visible labels, hints, and validation messages with their controls. Errors expose invalid state. Radix selection and toggle controls retain keyboard interaction and disabled semantics. Dialog provides a title, description, focus containment, Escape dismissal, and focus restoration. Tooltip complements an already named trigger and does not provide the only accessible name.

Picker searches option labels, descriptions, group names, and caller-provided aliases. Values are unique across groups; group names identify groups. Search is local to the opened picker and resets when it closes. Current selection appears on the trigger and with a check mark in the result list. Disabled options cannot activate, empty results have an explicit message, and Escape dismisses the innermost overlay first. Menus execute actions using menu semantics; pickers choose values using searchable list semantics. They share presentation without combining those roles into one component.

CommandPalette reuses searchable list presentation inside a named modal. Opening focuses the search field; selecting an action closes the palette; closing restores focus. Applications own command execution, shortcut registration, and whether shortcuts should run while editing or while another dialog is open. Kbd is a visual hint and registers no listener. Tabs provide arrow-key navigation and expose only the active panel.

SettingsRow can associate a visible label with a control through controlId; its description uses `<controlId>-description`. Callers connect that description with aria-describedby. A Switch can hide its own visual label when the surrounding row supplies it, retaining its accessible name.

Components retain visible focus indicators, and motion respects reduced-motion preferences. Callers supply accessible names for icon-only actions and all user-facing strings.

## Themes and Language

Applications load the shared stylesheet once and opt into base typography with `a13n-root`. Light is the default theme. The document element owns `data-a13n-theme`, ensuring portaled Select, Dialog, and Tooltip content inherits the same tokens. Per-subtree mixed themes are outside the supported contract.

English is the application default and fallback; Simplified Chinese is supported. Applications own language selection and persistence. Console stores a supported language locally; unavailable browser storage leaves the selection usable for the current session. Shared components have no translation runtime or locale persistence.

## Development Showcase

The showcase lives in `a13n-ui/dev` and runs through its own Vite entry. It includes foundations, primitive states, settings, and collection/detail compositions; navigation and filter data remain local demonstration state. It imports the public shared components and offers theme, language, and narrow-width previews, token specimens, and interactive control states. It is neither a Console route nor a public package export. Production applications do not import showcase code or styles.

Package checks type-check sources, exercise interaction contracts, and build the showcase. Application builds verify consumption through workspace exports. Browser checks cover focus, nested overlays, loading dimensions, theme inheritance, translated labels, and responsive layouts including narrow viewports.
