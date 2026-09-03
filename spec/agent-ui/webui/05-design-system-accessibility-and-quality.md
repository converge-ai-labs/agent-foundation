# Design System, Accessibility, and Quality

## Design Position

The WebUI is a simple, progressively disclosed local workstation, not a marketing page and not a generic administration template. Its design system gives ordinary work, detailed Debug inspection, and source configuration distinct visual contexts within one coherent shell. Visual polish cannot blur authority: retained versus live content, persisted versus process-local state, accepted versus draft configuration, and available versus unavailable control remain distinguishable without relying on color alone.

Radix UI primitives own accessible interaction behavior for dialogs, menus, popovers, tabs, tooltips, selects, switches, and related controls. Tailwind CSS consumes a small repository-owned semantic token layer. Feature components compose these primitives but do not bypass them with incompatible keyboard or focus behavior.

## Component Architecture

Components are organized by responsibility rather than page-specific duplication:

| Layer             | Responsibility                                                                                                                            |
| ----------------- | ----------------------------------------------------------------------------------------------------------------------------------------- |
| Foundations       | Semantic color, typography, spacing, radius, elevation, motion, focus, and density tokens                                                 |
| Primitives        | Buttons, inputs, badges, panels, scroll areas, disclosures, dialogs, menus, and feedback composed from Radix where behavior is nontrivial |
| Data display      | Timeline blocks, code, structured JSON/YAML, diffs, status facets, pagination, empty states, and diagnostic callouts                      |
| Domain components | Thread composer, decision batch, child execution card, configuration selectors, resource editor, account status, and catalog reference    |
| Feature layouts   | Workbench, Focus, Debug ledger/detail, configuration collection/editor, account area, and responsive shell                                |

A component can render a detached domain projection but cannot issue an HTTP request implicitly. Route feature boundaries own queries and commands and pass explicit values and callbacks downward. A component does not infer an App action from a color, label, or prior event.

## Semantic Tokens and Themes

The design system defines semantic CSS variables for at least:

- canvas, surface, elevated surface, and inset surface;
- primary and secondary text, muted text, and inverse text;
- border, strong border, focus ring, selection, and overlay;
- accent and accent contrast;
- success, warning, danger, and informational emphasis;
- retained, live, suspended, interrupted, and unavailable execution states;
- code, diff addition/removal, tool activity, and child activity accents.

Tailwind utilities reference semantic tokens instead of embedding feature-specific color values. Light and dark themes both meet the same contrast and state-distinction requirements. The default follows the operating-system preference; an explicit light, dark, or system choice is the only non-sensitive preference retained in `localStorage`.

Theme changes affect presentation only. They do not recreate the router, query client, focused stream, editor draft, or App command.

The production asset tree includes all fonts and icons it requires. System fonts are preferred; any bundled font has a reviewed license and no network fetch. Icons are decorative unless paired with an accessible name, and icon shape is never the sole state indicator.

## Workstation Layout

The shell exposes one stable global rail for Threads, Configure, and Debug, but each area uses only the regions its job requires:

| Area      | Wide layout                                                                  | Detail behavior                                                            |
| --------- | ---------------------------------------------------------------------------- | -------------------------------------------------------------------------- |
| Workbench | Project selector, attention-ranked filtered list, and one bounded preview    | Preview answers what needs the user; exact technical detail links to Debug |
| Focus     | Project context, optional filtered Thread collection, and one conversation   | Thread sheets are transient; there is no permanent debug inspector         |
| Configure | Resource-kind navigation, one collection, and one editor or conflict surface | Guided and exact source remain views of one draft                          |
| Debug     | Diagnostic navigation, one primary ledger/view, and optional selected detail | Detail is closed by default and no composer is present                     |

Area-specific composition keeps each primary surface visually dominant. Medium layouts reduce each area to the primary surface plus one drawer or tab. Narrow layouts show one primary region at a time with explicit accessible navigation. The same canonical route and focused controller survive pure layout transitions.

No critical action exists only on hover. Project collections and editors provide keyboard reordering for Projects and roots and announce the resulting order. Resizable Debug and Configure panes expose keyboard alternatives and bounded minimum/maximum sizes. User resizing is browser-session presentation state and does not enter App configuration.

Tables become labeled card rows or controlled horizontal regions on narrow screens rather than clipping actions. Dialogs that cannot fit become full-viewport sheets with preserved focus and dismissal semantics. The Threads composer remains reachable without covering the decision set or active control status; Debug never adds a second composer.

The workstation is desktop-first because it manages local roots, source text, and long-running agent activity, but every query, decision, control, debug selection, and conflict resolution remains operable at a narrow browser width. Responsive support does not imply native mobile installation or offline behavior.

## Accessibility

The WebUI targets WCAG 2.2 AA for its supported browser profile. In particular:

- every interactive element is keyboard reachable in a meaningful order;
- focus indicators are visible in both themes and never removed without replacement;
- dialogs and drawers trap and restore focus correctly;
- labels, descriptions, errors, and required state are programmatically associated with fields;
- color, position, motion, and icons are not the sole carriers of status;
- text and non-text contrast meet AA requirements;
- dynamic updates use restrained live regions and do not announce every token;
- reduced-motion preference removes nonessential animation and smooth scrolling;
- zoom and text resizing do not hide commands or require two-dimensional page scrolling for ordinary content;
- timeline, tool, child, and decision blocks use headings and landmarks that support structural navigation.

Streaming assistant deltas are not announced token by token. A polite live region announces meaningful boundaries such as response completion, pending decision, failed operation, or child completion. Steering and cancellation confirmations name the exact target in accessible text.

Keyboard shortcuts are discoverable, scoped, and never shadow ordinary browser or editor behavior. At minimum, focus composer, open command palette, return to Workbench, open the selected work detail, and move between Threads and the corresponding Debug view have configurable or documented bindings. A shortcut never approves, denies, deletes, logs out, or bypasses a confirmation through one unmodified keystroke.

## Forms and Editors

Inputs expose visible labels and persistent error text. Validation does not rely on toast messages. A failed submit focuses the first invalid field while preserving the complete draft. Ordered lists support both pointer drag and keyboard move controls and announce the resulting position.

CodeMirror uses language-aware YAML and Markdown modes, line numbers, search, and accessible text editing without replacing browser clipboard behavior. The editor does not intercept application shortcuts while text focus is active. Source diagnostics map to line/column ranges when the App supplies them and remain available in a separate list for screen-reader navigation.

A guided/source mode switch preserves one draft and announces parse failures. Diffs use textual markers and accessible labels in addition to color. Conflict controls name base, current source, and local draft explicitly.

## Feedback and Attention

Persistent state uses inline banners or panels. Toasts are reserved for short confirmations that do not require later reference. An error that blocks submission, invalidates access, resets a stream, or requires conflict resolution never exists only as a disappearing toast.

Loading presentation distinguishes:

- initial content absence;
- refreshing retained data while stale content remains usable;
- reconnecting a live stream;
- submitting an exact command;
- waiting for an App operation after admission.

Skeletons are used only where the final geometry is predictable. They never make a destructive or authority-bearing control appear enabled before its projection loads.

Attention badges are bounded and derived from visible App facts. They cannot become an unread notification ledger or imply that the browser durably acknowledged an event.

## Safe Content and Browser Policy

All model, tool, resource, import, account, and diagnostic content is untrusted. The renderer:

- escapes plain text and disables raw HTML in Markdown;
- allows only reviewed URL schemes and applies opener isolation to external links;
- never evaluates JavaScript, template expressions, arbitrary SVG, remote component definitions, or tool-returned CSS;
- applies size and nesting bounds before syntax highlighting, diffing, or expanding structured values;
- renders omitted or truncated values as explicit states;
- avoids placing API keys or credential-shaped values in DOM attributes, URL state, clipboard defaults, or client error reports.

The production document uses a restrictive Content Security Policy compatible with local static assets, same-origin API access, necessary inline-free Vite output, and provider OAuth navigation. It does not enable arbitrary remote scripts, frames, connections, or object embedding. Development mode can use the minimum additional Vite connections required on loopback without weakening production policy.

The application registers no service worker. A stale open tab detects incompatible API assets through version/schema checks and requests a full reload rather than serving an offline mixture of releases.

## Performance and Resource Discipline

The Threads shell, Project selector, Workbench, and Focus interaction path form the initial bundle. Debug ledgers and payload viewers, Project and other configuration editors, diff support, catalog management, account flows, and other heavy routes load on demand. CodeMirror and language support do not enter the initial Threads bundle.

Long Thread, resource, catalog, and child collections use server keyset pagination. Long timelines and structured views use virtualization or bounded expansion while preserving keyboard navigation and copy semantics. The browser never fetches every Thread or mounts every transcript to calculate Project recency or attention.

Detailed stream deltas are reduced in order and committed to React at most once per animation frame under sustained output. Summary invalidations for the same query family are coalesced. Rendering performance optimizations cannot reorder events, drop a terminal boundary, or delay authority-bearing controls behind a visually batched stale projection.

Leaving a route aborts its requests, focused stream, workers, syntax tasks, and timers. Code-split failures produce a recoverable reload state. No feature creates an unbounded in-memory event ledger; retained history remains server-owned and provisional focused state is discarded on route change or reset.

## Test Architecture

Vitest owns pure browser boundaries: access bootstrap, normalized query and invalidation mapping, SSE framing and cursor semantics, high-water cutover and reset handling, focused event reduction, command and draft construction, conflict state, and route decisions. Testing Library exercises components through roles, labels, visible text, keyboard input, and focus rather than private component state. Automated accessibility assertions cover primitives and representative feature states in both themes.

MSW uses fixtures validated by the generated runtime schemas to exercise the generated client across finite queries, commands, authentication, conflicts, validation failures, unknown outcomes, and stream reconnection. These tests do not substitute for Python tests that mount the actual Web adapter and verify access enforcement, static routing, OpenAPI bodies, error mapping, fresh-watch snapshot cutover, resumable event replay, explicit cursor and subscriber-gap resets, disconnect cleanup, and `AgentUiApp` method correlation.

Playwright runs the built static application against a real local Web adapter and disposable Agent UI data and configuration roots. Its critical journeys cross access bootstrap and direct navigation; Project creation, Project and root ordering, Project-filter switching, source-digest conflict, and deletion without rewriting retained Threads; Workbench-to-Focus supervision; root and child interaction with live-to-retained reconciliation and exact controls; complete deferred decisions; Threads-to-Debug correlation without a second composer or stream; digest-protected resource management and rebase; accepted-generation diagnostics; compatible account actions; restart and stream recovery; and keyboard-only accessibility. End-to-end assertions target semantic state and authority boundaries rather than pixel snapshots alone. Visual regression can cover the shell and core states, but it does not replace interaction, accessibility, or protocol assertions.

## Browser Compatibility

The supported profile is current evergreen desktop Chromium, Firefox, and Safari with native ES modules, `fetch`, streaming response bodies, `AbortController`, History API, `sessionStorage`, CSS custom properties, and modern accessibility APIs. The build can transpile syntax within that profile but does not ship legacy-browser polyfills or an alternate non-streaming application.

A browser that cannot provide authenticated fetch streaming receives an explicit unsupported-browser state rather than degraded polling that changes live semantics. Narrow viewport support applies within the same browser profile.

## Packaging and Validation

The frontend validation boundary includes formatting, strict TypeScript, production build, unit/component tests, generated-contract freshness, and critical browser tests. The final asset validator verifies that every HTML-referenced local asset exists and matches the packaged manifest. Source maps, test fixtures, development credentials, and external registry state do not enter the production static tree.

The built frontend is tested from the same wheel/sdist packaging path used for release. Rebuilding a wheel from the sdist never invokes npm, Vite, CodeMirror tooling, Tailwind, or contract generation.

## Trade-offs

Radix plus Tailwind requires the repository to own more composition and visual detail than a full component suite, but it keeps behavior accessible and the workstation identity controllable. A strict static SPA cannot use server rendering for initial content, but local same-origin latency and a stable shell make that cost small while preserving Python-only runtime installation. Comprehensive stream and conflict tests add upfront work, but those are the boundaries where a superficially responsive UI can otherwise misrepresent execution or overwrite user configuration.

## Invariants

01. Visual treatment never collapses retained/live, persisted/local, accepted/draft, or available/unavailable distinctions.
02. The Threads area remains Project-filtered, simple, and action-oriented; Debug remains detailed and read-only; Configure remains the desired-resource management surface.
03. Radix primitives and semantic tokens provide one accessible component foundation.
04. Every critical operation remains keyboard operable and understandable without color or motion.
05. Untrusted content is rendered as inert text or bounded structured data.
06. The production application loads no remote executable, font, style, or configuration asset.
07. Route splitting keeps heavy Debug, management, and editor code out of the initial Threads route.
08. Performance batching preserves event order and never delays control authority behind stale visual state.
09. Pure, adapter, and real-browser tests cover different boundaries and do not substitute for one another.
10. The supported browser must provide authenticated fetch streaming; the UI does not invent polling semantics.
11. Published Python artifacts contain complete prepared browser assets and require no Node.js at runtime or sdist-to-wheel build time.
