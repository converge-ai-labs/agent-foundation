# Frontend Design System

## Design Position and Ownership

`frontend/packages/a13n-ui` provides shared React primitives, compositions, and design tokens for browser applications. The interface uses restrained surfaces, clear typography, readable secondary text, consistent spacing, and visible focus indicators. Hierarchy comes from alignment, spacing, typography, and purposeful color; controls make their behavior recognizable before activation.

Coss UI registry components provide the visual and interaction foundation, using Base UI for focus, overlays, selection, and menu semantics and DayPicker for calendars. Tailwind CSS owns shared component styling and semantic theme utilities. CSS Modules remain available for application-specific layouts. The [repository model](../repository-model.md#frontend-workspace) owns workspace and packaging boundaries.

Product layouts, navigation, conversation rendering, protocol state, data fetching, persistence, and translations belong to applications. Shared components do not import applications or interpret domain states. Badge variants describe visual meaning; applications map domain states to variants and translated labels.

## Sources of Truth

| Owner                                                                       | Contract                                                                 |
| --------------------------------------------------------------------------- | ------------------------------------------------------------------------ |
| [Theme definitions](../../frontend/packages/a13n-ui/src/styles/theme.css)   | Semantic colors, light and dark themes, and Tailwind mappings            |
| [Application tokens](../../frontend/packages/a13n-ui/src/styles/tokens.css) | Application aliases, brand typography, and shared scrollbar tokens       |
| [Typed exports](../../frontend/packages/a13n-ui/src/index.ts)               | Public component signatures                                              |
| [Source provenance](../../frontend/packages/a13n-ui/coss-source.json)       | Imported registry revision, source paths, license, and local adaptations |
| This document                                                               | Observable visual and interaction rules and their ownership              |

`src/components`, `src/hooks`, and `src/lib` contain imported registry sources. Only the MIT-licensed UI registry is included. `src/patterns` contains reusable field, modal, search, disclosure, and settings compositions; `src/brand` contains project identity assets. Applications use public components directly and keep business-specific compositions in their owning feature or shared directory. Recurring control behavior and styling are corrected in their shared owner rather than overridden independently in each screen. Shared defaults remain lower in specificity than component styles so import order cannot override component appearance.

## Visual Hierarchy and Layout

The application frame, content canvas, and elevated surfaces have distinct roles. Related controls form recognizable groups through spacing or a quiet surface. Section headings and explanations sit outside their groups. Section boundaries primarily use whitespace. Fine dividers separate adjacent settings rows within a shared surface, tabular rows, and menu groups when the boundary helps scanning. They use a low-contrast semantic border color, align with the content inset, and leave balanced space on both sides. Dividers do not frame every field, repeat an enclosing border, or replace the space between sections. Extra cards, nested borders, and resting shadows do not accumulate around every section or property.

Page headings stay proportional to interface text. Primary page actions align with the heading. Search and filters form a separate row above the content, with room between controls. Navigation uses compact Sidebar rows, small gaps within groups, and modest separation between groups. Navigation and view selection use neutral surfaces; accent color emphasizes primary actions and active controls. Identity icons may use a restrained color accent.

Settings align short controls beside explanatory copy; long inputs and complex configuration use the available content width. The gap between sections exceeds the spacing within a field or row. Narrow containers stack labels above controls and keep names, explanations, and actions readable without horizontal overflow. Tables use compact rows and align action-column headings with their controls.

Standard controls serve forms; compact controls serve toolbars, settings rows, and properties. Compact density does not reduce text readability or eliminate keyboard focus. Ghost controls suit named inline actions and editable properties whose context makes interaction clear. Search and ordinary value selection retain visible control boundaries.

The package owns the bundled Space Grotesk Bold typeface, its license, and the `--a13n-font-brand` token. `Wordmark` renders the a13n name with shared weight and letter spacing; applications choose its size and placement. A decorative `Logo` can accompany an already named brand. Product body text retains the standard interface font.

## Control Meaning and Configuration

| Intent               | Affordance and behavior                                                                                                                                                                        |
| -------------------- | ---------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Enter text           | Input or Textarea has a subtle border and a distinct editable surface, including inside a tinted group.                                                                                        |
| Select a value       | Select, ChoiceField, or SearchPicker shows the current value with a trailing arrow and a subtle control border. Activation opens an option list.                                               |
| Expand configuration | DisclosureSection has a leading chevron that rotates with its expanded state. Its trigger and content share one continuous, softly tinted region with coordinated padding and rounded corners. |
| Add a resource       | A named action button uses a plus icon. An inline selection region stays grouped with its trigger while open.                                                                                  |
| Edit identity        | An edit action sits beside the identity it changes and has an accessible name.                                                                                                                 |
| Manage a resource    | A separate action menu groups secondary management operations. Destructive actions remain clearly named and use the applicable confirmation flow.                                              |

Simple configuration stays visible beside the item it configures. A single version field or a short scope field does not need a separate disclosure. Disclosures serve optional groups with enough complexity to benefit from progressive disclosure. A concise summary can expose their current state while closed. Collapsing configuration preserves the draft; validation reveals the relevant group and identifies the field.

Labels explain purpose and descriptions add useful context without repeating the label or current value. Metadata and editor tools appear when they help the user make a decision or perform a distinct task. Character counts without a meaningful limit, redundant format hints, and preview or expansion controls without a separate editing need do not occupy the default form. Long content is handled by bounded scrolling.

Applications distinguish changes saved immediately from changes held in a draft. Save feedback identifies pending, successful, and failed work. Actions requiring persisted configuration expose that dependency when a draft prevents activation. The owning product contract defines which fields save together; shared components do not infer persistence boundaries.

## Fields and Focus

Numeric Input fields hide native visual stepper buttons while preserving numeric validation, direct entry, and keyboard stepping.

Input, Textarea, selection triggers, and composers use subtle control borders without stacked resting shadows. Focus adds a fine indicator; error borders and messages remain distinguishable. Buttons, switches, checkboxes, tabs, and other keyboard targets retain visible focus at both control densities. Disabled explanations remain readable.

A composite input owns its boundary and focus treatment once, on the enclosing control. Inner Input or Textarea instances use their unstyled composition mode when the parent supplies that treatment. Unstyled mode preserves field semantics; the caller owns the visible boundary and focus indicator. Searchable composites apply this rule to their search row, and composers apply it to their text-entry surface.

FormField associates labels, descriptions, and validation messages with Input or Textarea and exposes invalid state. ChoiceField supplies equivalent semantics for Select. Choice options accept text labels and optional decorative icons. Fields and icon-only actions always have an accessible name. Tooltips complement a named trigger and do not provide its only accessible name.

SettingsSection supports grouped and plain presentations. Grouped settings use the muted surface against the page canvas, with rounded corners and an external secondary heading. Adjacent items share a single fine divider inset by the group padding; there is no leading or trailing rule. The page composition owns spacing between sections so component margins do not add a second gap. Plain sections use whitespace without a surface. SettingsRow pairs a name and optional explanation with a control, stacking in narrow containers. Its `controlId` associates the visible label with the control; its description uses `<controlId>-description`, which callers connect through `aria-describedby`. A Switch uses the associated row label as its accessible name.

## Identity Images and Save Feedback

Identity image controls compose one preview with adjacent upload/removal actions and concise format guidance. Keep the preview large enough to inspect, use a neutral fallback, and preserve the same image across list and detail surfaces. Supporting labels and file guidance belong to the image control rather than separate competing rows.

When automatic and manual saving coexist, identify the automatically saved setting beside that setting using a compact, legible status treatment. Distinguish its idle, saving, saved, and failed states; do not rely on low-contrast descriptive text or color alone. Keep labels short enough for their actual layout.

## Scrolling and Long Content

ScrollArea and ScrollBar own shared content scrolling. Native controls and semantic regions that scroll directly use the public `a13n-scrollbar` utility from the same stylesheet. Both presentations consume shared size, thumb, and hover tokens: a thin rounded thumb, transparent track, and stronger hover feedback. Scrolling retains native keyboard, pointer, and touch behavior. ScrollArea's bars appear on hover or scrolling; native bar visibility follows browser and operating-system behavior.

Textareas honor their row count, bound vertical resizing, and scroll internally when content exceeds their height. The shared Textarea owns the default height limit; applications can choose a smaller bound for compact editors. Typing does not grow a form without limit or push its remaining controls indefinitely downward. Native textareas retain their own scroll viewport rather than nesting inside a second ScrollArea.

Dialogs and bounded collection regions keep content reachable without clipping it behind fixed controls. Scrollable areas remain usable in narrow layouts and both themes.

## Actions and Overlays

Buttons preserve native button semantics and default to a non-submitting button. Disabled or loading buttons do not activate. Idle text buttons have symmetric padding and no empty icon slot. Loading preserves content and dimensions, prevents activation, and exposes busy state. Its overlaid spinner is decorative; standalone loading indicators receive translated status text.

ModalFrame composes Dialog parts for resource forms. Dialog provides a title, description, focus containment, Escape dismissal, and focus restoration. Its title and close control remain visible while content scrolls within the rounded surface; an optional footer stays outside that scroll area. A form-owned footer marked `data-a13n-form-actions` remains sticky within the scroll area, retaining native form submission and leaving the last fields reachable. Complex forms use the wide size, with viewport-bounded dimensions at both sizes.

SearchPicker searches option labels, descriptions, group names, and caller-provided aliases. Values are unique across groups; group names identify groups. Search is local to the opened picker and resets when it closes. Current selection appears on the trigger and with a check mark in results. Disabled options cannot activate, empty results have an explicit message, and Escape dismisses the innermost overlay first.

Menus support nested action groups and checked current items with Base UI submenu keyboard behavior. Pickers choose values using searchable list semantics. Workspace navigation uses a compact menu; search belongs in pickers for longer selectable resource lists. Tabs support arrow-key focus and keyboard activation; inactive panels are unavailable after their exit transition.

Calendar, Popover, and ChoiceField provide date and time entry primitives. Console's DateTimeField owns translated labels, active locale, draft until Apply, clearing, dismissal, and validation of local date-time strings. Applications own API timestamp conversion. Nonexistent local times are rejected.

Base UI controls retain keyboard interaction and disabled semantics. Motion respects reduced-motion preferences. Callers supply all user-facing strings.

## Themes and Language

Applications load the shared stylesheet once and opt into base typography with `a13n-root`. Light is the default theme. The document element toggles the `dark` class, ensuring portaled Select, Dialog, and Tooltip content inherits the same tokens. Per-subtree mixed themes are outside the supported contract.

English is the application default and fallback; Simplified Chinese is supported. Applications own language selection and persistence. Console stores a supported language locally; unavailable browser storage leaves selection usable for the current session. Shared components have no translation runtime or locale persistence.

## Development Showcase and Validation

The showcase lives in `a13n-ui/dev` and runs through its own Vite entry. It imports public components and demonstrates foundations, primitive states, settings, and collection compositions. Value selection, inline configuration, grouped disclosures, focus, and bounded scrolling are represented as interactive examples. Theme and language controls use local demonstration state and responsive layouts. The showcase is neither a Console route nor a public package export; production applications do not import its code or styles.

Package checks type-check sources, exercise interaction contracts, and build the showcase. Application builds verify consumption through workspace exports. Browser checks cover focus, nested overlays, loading dimensions, theme inheritance, translated labels, expanded grouping, bounded scrolling, and narrow layouts.
