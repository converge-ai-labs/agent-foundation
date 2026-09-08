# Frontend Design System

## Design Position

`frontend/packages/a13n-ui` provides shared React primitives and design tokens for browser applications. Radix owns complex interaction behavior; CSS Modules and namespaced CSS custom properties own presentation. The system uses restrained surfaces, clear typography, readable secondary text, consistent spacing, and thin visible focus indicators.

The [repository model](../repository-model.md#frontend-workspace) owns workspace and packaging boundaries. Product layouts, navigation, conversation rendering, protocol state, data fetching, and translations belong to applications. Shared components do not import applications or interpret domain states.

## Sources of Truth

[Token definitions](../../frontend/packages/a13n-ui/src/styles/tokens.css) own concrete color, typography, spacing, sizing, radius, focus, motion, and layering values. [Typed exports](../../frontend/packages/a13n-ui/src/index.ts) own component signatures. This document defines behavior and ownership without duplicating their value tables.

The initial public primitives are Button, Input, Select, Checkbox, Switch, Badge, Spinner, Tooltip, and Dialog. Badge tones describe generic visual meaning; applications map domain states to those tones and translated labels. Select options accept a text label and an optional decorative React icon.

## Interaction and Accessibility

Buttons preserve native button semantics and default to a non-submitting button. Disabled or loading buttons do not activate. Callers pass a stable boolean loading prop to reserve the icon slot; normal and loading labels participate in sizing without duplicating child content. Loading exposes busy state; Spinner itself is decorative.

Fields associate visible labels, hints, and validation messages with their controls. Errors expose invalid state. Radix selection and toggle controls retain keyboard interaction and disabled semantics. Dialog provides a title, description, focus containment, Escape dismissal, and focus restoration. Tooltip complements an already named trigger and does not provide the only accessible name.

Components retain visible focus indicators, and motion respects reduced-motion preferences. Callers supply accessible names for icon-only actions and all user-facing strings.

## Themes and Language

Applications load the shared stylesheet once and opt into base typography with `a13n-root`. Light is the default theme. The document element owns `data-a13n-theme`, ensuring portaled Select, Dialog, and Tooltip content inherits the same tokens. Per-subtree mixed themes are outside the supported contract.

English is the application default and fallback; Simplified Chinese is supported. Applications own language selection and persistence. Console stores a supported language locally; unavailable browser storage leaves the selection usable for the current session. Shared components have no translation runtime or locale persistence.

## Development Showcase

The showcase lives in `a13n-ui/dev` and runs through its own Vite entry. It imports the public shared components and offers theme, language, and narrow-width previews, token specimens, and interactive control states. It is neither a Console route nor a public package export. Production applications do not import showcase code or styles.

Package checks type-check sources, exercise interaction contracts, and build the showcase. Application builds verify consumption through workspace exports. Browser checks cover focus, nested overlays, loading dimensions, theme inheritance, and responsive layouts.
