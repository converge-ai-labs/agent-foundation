# Harness WebUI Design Guidelines

## Scope

This document owns Harness presentation. The [shared design system](../../frontend/design-system.md) owns common primitives and accessibility; [workbench interaction](04-workbench-interaction.md) owns behavior and state. Console dimensions and layouts do not apply here.

Harness is compact, quiet and softly contrasted. Settings supply the grouping and label/value language across editors, dialogs, Activity and chat options. Cursor informs density and restraint; it is not an external authority over accepted Harness behavior.

## Geometry and Type

| Role                                                                      | Baseline                                        |
| ------------------------------------------------------------------------- | ----------------------------------------------- |
| Relationship spacing                                                      | 4, 8, 12, 16, 24, 32, 48, 64px                  |
| Desktop controls / menu rows                                              | 32px                                            |
| Sidebar rows                                                              | 30px high; 2px between adjacent fills           |
| Touch targets                                                             | At least 44px; determined by pointer capability |
| Radius: control / group and overlay / composer                            | 8 / 16 / 20px                                   |
| Reading width: saved conversation / New chat / settings                   | 800px / 800px / 760px                           |
| Standard form dialog                                                      | 480px wide; 24px desktop and 16px narrow insets |
| Field groups / page sections                                              | 16 / 32px gaps                                  |
| Default type: navigation / interface / prose / composer / code / terminal | 13 / 14 / 15 / 16 / 12 / 13px                   |
| Type: content heading / page and dialog title                             | 16 / 20px                                       |
| Weight                                                                    | 400 regular; 500 interface emphasis             |
| Line height: prose / large heading                                        | About 1.7 / 1.1–1.3                             |

Use the existing system sans-serif and monospace stacks. The six independent sizes are viewer-local preferences under [Appearance](04-workbench-interaction.md#appearance); interface titles and secondary text scale from the interface baseline. De-emphasize supporting text through color or weight. Heading levels must be visibly distinct. Narrow touch editors retain at least 16px effective input text to avoid focus zoom without changing the stored size; narrow desktop windows retain compact controls. Navigation rows grow when needed to fit their text while retaining action space and touch targets. Appearance preferences leave browser root font size, zoom, spacing and layout widths unchanged.

Spacing expresses relationships: within an item < between peers < between groups < outer breathing room. Semantic levels differ clearly, usually by about 1.5× or more; this is not a multiplier for every phone gutter. Optical offsets, hairlines and control geometry are separate from relationship spacing.

Labels sit closer to their content than the preceding group. Group insets accommodate internal spacing; peer groups have at least that much separation. Divider whitespace is balanced. One container owns each inset. Equivalent rows share heights; wrapping adds only the space required.

## Color, Borders and Hover

Light-theme baselines: canvas `#fefefe`, text `#333333`, sidebar `#fafafa`, muted surface `#f9f9f9`, hover 2.5% black, selected 4% black. Use theme tokens, including their dark equivalents, rather than local shades.

Disabled primary and secondary actions use a quiet surface and muted label rather than a dimmed solid primary fill; disabled ghost/icon actions stay unfilled. Press feedback is immediate fill/color, and keyboard focus uses the shared 2px signal outline with 2px offset.

Placeholder text uses a quieter theme tone than secondary labels across composer, form and search inputs. Entered text retains normal contrast; increased-contrast settings strengthen placeholders.

Ordinary rows, cards and buttons stay flat on hover and press: no elevation, scaling, movement, weight jump or changing hit-area dimensions. Feedback uses light fill or color. A row has one background; nested overflow controls do not add another filled rectangle. Transcript disclosures use text emphasis only.

Floating menus, popovers and dialogs retain shared overlay shadows. When the floating surface is a single action, the button owns the full face, hit area and shadow. Hover emphasizes its label or icon without an inset fill; keyboard focus outlines the whole button. Borders identify controls, floating boundaries or dense row separation; ordinary grouping uses space and tone. Avoid nested frames. Where rounded surfaces are nested, inner radius is outer radius minus inset, bounded at zero.

## Alignment and Identity

Titles, text starts and trailing controls follow a few shared axes. Same-row text, icons and controls are optically centered. Short values stay compact; long paths wrap or move below labels before columns become unusable.

The workbench header and sidebar brand share a 48px desktop alignment band. Settings and other document pages keep participant presence inside a named titlebar on that axis rather than floating in a page corner. Unnamed pages use a generic presence label. Repeated participant IDs appear once; distinct participant IDs are not merged by matching names, initials or colors. The New chat header does not repeat the centered entry heading on desktop. At widths up to 760px, the chat header retains at most two direct trailing actions; Share, People and supporting-view controls remain available with icons and labels in the secondary menu. The workbench header keeps Project and chat titles on one line. Chat titles have a bounded reading width even on wide screens and ellipsize further as space narrows; actions retain their space. A truncated Project name keeps its separator visible, and narrow layouts omit the Project breadcrumb. Hover exposes the complete names; chat actions retain the full-title Rename field for keyboard and touch. The browser document title uses a bounded preview. These display limits never rewrite saved titles.

Navigation icons are 16px; disclosure carets are 12px. Model logo and name remain adjacent everywhere, including selectors, settings and usage attribution, using the shared brand registry and fallback. Transcript preparing/working uses the animated a13n logo with reduced-motion support.

Reserve space for appearing actions and carets. Hover, loading and expansion never shift labels or resize targets. Keyboard focus stays visible; touch retains usable affordances without hover.

## Navigation

**New** has one leading plus; **Search** and **Drafts** share its text start. Project and chat rows retain the thin gap between fills. Brand, page title and header actions share a vertical center. Active, New results and History subheaders align with chat text, beneath their Project identity. Only collapsed Projects show a quiet unboxed unread count; expanded groups show the count once in New results. Navigation has bottom breathing room and a soft scroll-edge fade above the account footer. Touch rows use a consistent 44px rhythm with quiet, reachable overflow controls.

A Project's leading folder becomes a small caret only while hovered; pointer leave restores the folder even when expanded, selected or focused. Focus uses the normal ring. Coordinators retain their distinct leading worker disclosure, medium title and indented guide under [Coordinator Entry](04-workbench-interaction.md#coordinator-entry).

Rows drag directly without a grip icon. Use a compact preview and thin insertion indicator; surrounding rows stay stationary until drop and dragging does not activate them. [Entry and Navigation](04-workbench-interaction.md#entry-and-navigation) owns ordering, transfer eligibility, keyboard alternatives and resize limits. Narrow layouts use the drawer, with the same logo-and-Harness lockup as desktop. A translucent dark scrim fades while the drawer slides with the sheet easing and a soft edge shadow. Desktop navigation collapse and expansion use transform motion without intermediate text reflow; Project disclosures animate their grid height and caret. Reduced motion removes these transitions.

## Conversation and Composer

### Reading and Execution Details

Saved conversations and New chat use a centered column capped at 800px with symmetric gutters; transcript, Activity row and composer share an axis. Left navigation retains its existing layout and controls. User bubbles fit content within a bounded width, using the quiet surface and 16px corners. Message actions reserve their geometry, reveal on hover/focus with a fine pointer and stay visible on touch; copy confirmation changes the glyph without shifting adjacent timestamps. User messages and intermediate and final assistant prose share primary text color, the conversation size (15px by default) and 1.7 line height. Markdown tables follow that size; heading levels use 1.5, 1.25, 1.125 and 1em. Inline code stays relative to surrounding prose. Code blocks and line numbers use the independent code size; their toolbar labels use the interface size and sans-serif stack. Horizontal code, formula and table readers retain native swipe scrolling without a persistent scrollbar on touch. Interface labels and tool rows retain their compact scale. Tool and Thought labels use quiet gray, regular text; only hover or focus strengthens them, not expansion.

Completed work collapses behind **Worked for …**, leaving the final response visible, under the [conversation contract](04-workbench-interaction.md#conversation-and-shared-composer). Omit **Work log**, tool-count summaries and tool elapsed times. Recorded Run/Thought timing uses muted tabular figures. While a thought streams or a tool call is pending, a bright highlight sweeps across its label and the corresponding execution preview. Routine pending calls use the highlight alone. Tool rows and group summaries omit receiving, waiting and running status labels, counts and pulsing dots. Settled steps, reduced-motion preferences and forced colors use static text. Assistant prose, execution labels and the preparing or working logo align with the leading edge of the composer frame.

**Thought** is a default-collapsed, unboxed disclosure. Expanded text grows to 144px, then scrolls in a keyboard-accessible region. Conversation disclosures have no leading category icon: use a trailing 12px caret on hover/focus, visible without hover on touch. Keep label and hit-area geometry stable. Thought and execution panels use the shared grid-row disclosure motion; only newly arriving live turns receive a subtle opacity/translate entrance, never restored history.

Input, output, commands, changes and errors remain inspectable in ordinary mode; Debug is not required for essential details. Preserve formatted/raw views and exact-source copy. Single-call summaries identify the target without repeating the operation heading inside. Output previews use compact headers and quiet surfaces, without nested decorative rails. Expansion preserves reading position and width.

### Composer and Usage

Keep the editor above a single row of attachment, options, Model and Send controls. Reserve one line for input even when the draft is empty. Avoid accumulated editor gutters, oversized action backgrounds and a disproportionately large Send button. Send uses a quiet neutral disabled face and a crisp primary enabled face; the Send/Stop glyph transition preserves the control's geometry. On touch, its compact face sits inside a 44px target. Narrow model summaries retain the brand and a truncated name, leaving Thinking in the picker rather than crowding the action row.

New chat keeps its centered hero, Project selector and composer on desktop. At narrow widths, the hero and Project selector sit above a bottom-docked composer. Composer and Activity use 16px narrow gutters. The composer owns its bottom breathing room and safe-area inset; when the keyboard covers the bottom safe area, that inset is not added again. The workbench fits the visual viewport so the transcript and composer remain in one flex layout rather than overlapping behind the keyboard. Editor growth is content-driven up to a bounded internal scroll area; dictation retains the draft's geometry.

The Thinking picker uses the current level as its visible heading. A stepped slider runs from a light tint of the a13n mark to its shadow color. The heading previews the level during dragging, and a reset action restores Default.

For ordinary chats, one compact row immediately above the composer holds Activity at the left and context/cost at the right. Bot keeps context/cost visible on the right and shows only the left-side Activity controls in Debug mode; attachment and microphone controls stay in the composer. Sustained live connection status uses quiet, unboxed text between them, collapsing to an accessible connection icon when space is limited; activation opens details on keyboard and touch as well as pointer. Its appearance does not shift the composer or usage controls. Narrow rows consolidate Activity shortcuts into their existing menu before crowding status and usage. There is no second routine footer row. Context shows a compact token count preceded by a brand-purple occupancy ring, or the context icon when occupancy is unknown; cost retains `$`; cache belongs inside the context popover. Units and unavailable values follow the conversation contract.

Usage popovers lead with the requested figure and unit, then aligned supporting rows. Breakdowns expand inside one bounded reader; help carries accounting explanations. Avoid a card, subtitle or large gap for every metric. Scope changes preserve position and alignment.

### Reader Edges

The input rail sits in the gutter beside the centered reading column, with a distinct active mark and keyboard/hover previews. Narrow layouts place its unboxed history trigger in the Activity shortcut group above the composer, not over prose or in a permanent extra row. The directory opens upward in a bounded popover.

Turn navigation lands 56px below the reader's top boundary, clearing the header fade. An interruptible smooth scroll respects reduced motion. A quiet jump-to-latest action appears while reading above the bottom, even before new output arrives, and fades/translates without changing reader geometry. Initial entry lands immediately; streaming growth follows smoothly until the reader scrolls away. Earlier history preserves the visible message anchor, including gestures made while a page is loading.

Content fades continuously toward the header and composer. No hard seam, ghosted text, text-shadow halo or blurred strip crosses a readable line. Apply the effect at the reading boundary; keep controls crisp and content fully readable away from it. Prose, tables and code retain their geometry through scrolling.

The header divider appears only with the supporting right pane open. Saved ordinary empty chats have no central instructional hero; the New draft retains its entry heading and Project selector. Coordinator introductions follow their behavior contract.

## Background Work

The floating card sits 8px below the chat header and clears the chat page padding and transcript scrollbar gutter (48px from the chat area's right edge; at least 12px plus safe-area clearance on narrow layouts). Narrow cards stay above the composer and scroll internally when height is limited. Entry fades and translates from 8px above over the base duration with ease-out; exit reverses more quickly with ease-in, and reduced motion removes travel. It is about 320px wide on desktop, using the elevated surface, overlay shadow and 16px radius. Subagents and Tool calls each show at most three single-line rows and a quiet View all action. Names ellipsize with full names and previews in tooltips. A 16px character/icon, text start, compact state/duration and reserved Stop slot share axes. Commands use the shared code size; metadata uses secondary text and tabular figures. Touch rows retain 44px targets. Use one flat hover fill.

The Subagents and Tool calls history tabs reuse those rows with scrolling and Show more; the tab header names the section, so rows start without a section heading. Item tabs reuse the resource strip's truncation and close controls. Child tabs have no composer; their Stop action sits beside state in the header, matching tool tabs. Accepted Stop shows the shared loading indicator in the same slot until terminal state arrives. Saved/live readers reuse transcript components and checkpoint cut-over; notices remain quiet execution links. Tasks and Notes remain the composer's only work shortcuts.

Child identity is a small generated SVG glyph: a candy-colored rounded polygon with legible eyes. The Thread ID seeds its palette entry, shape, rotation, corner rounding, eye style, gaze and tilt. Identity glyphs remain still; status uses separate text or a reduced-motion-aware state icon, never identity hue or idle glyph breathing. The shared identity role owns the palette and contrast rules. Empty, loading, error and narrow states preserve alignment.

## Settings, Dialogs and Popovers

- Settings groups follow the page title and use 16px horizontal insets with quiet inset separators between peer rows. Headings sit 8px above their content. Rows have 8px vertical insets and a 48px minimum; controls center against the complete label/description block. Display language is an ordinary label/value row in General. Short numeric controls share a bounded width. Nested disclosures and authentication compositions reuse the group's surface and inset, without doubled row padding.
- Appearance resets appear only for changed values while reserving their target space. The preview uses the actual navigation, message, editor and composer type roles. Resource lists retain a leading name/mark, ellipsized secondary identifiers and a reserved trailing action column on phones.
- Form dialogs use the geometry above. Text entry uses available width; short selectors may sit beside labels. Wider complex editors and readers need actual content to justify the width. Advanced sections and folder browsers scroll within a bounded body; actions stay reachable and own the footer inset once.
- At widths below 640px, form and confirmation dialogs use full-width bottom sheets with 16px top corners, safe-area bottom spacing and keyboard-aware visible-viewport bounds. Bodies scroll while actions stay reachable. Footer actions stack full-width in reverse document order (primary first, then Cancel and supporting actions); desktop actions remain right-aligned. Sheets enter with the shared 320ms sheet curve and exit over 120ms ease-in. Reduced motion removes travel. Long values stay inspectable.
- Menus use one 4px outer inset, aligned icon/text columns and trailing states. Separate functional groups only. Small menus need no page-style title or close button; nested choices retain one Back header. Optional explanations use help; consequences stay visible.
- Menus, popovers and selectors enter with a 0.96-to-1 scale and fade from the trigger-side origin over 200ms; exit uses a faster 120ms ease-in. Dialogs fade and scale with a separately fading backdrop. Sheets slide over 320ms with the shared sheet curve. Reduced motion collapses durations to 1ms; popup dimensions and positioner coordinates never animate.
- Result notices use the shared compact top-center deck: at most three active cards, one-line title, two-line preview and inline action. Hover/focus expands the deck and pauses expiry; pointer drag/swipe dismisses cards. Bursts replace the oldest cards without a backlog, and mobile uses safe-area top spacing and 16px gutters. This changes presentation, not event eligibility, receipt deduplication, or conversation-owned recovery.
- Popups fit the viewport independently of trigger width. Opening a dialog hides its originating menu through entry and exit; closing restores focus without reopening the menu. Form footers pair Cancel and the primary action; information-only dialogs use Close.
- Tabs retain recognizable selected, hover and focus states with aligned content edges. Reuse the accepted treatment for each role; do not interchange navigation tabs and mode selectors.

## Connectors

Every Connector follows the same account-first presentation in Settings and conversation setup. Use the backend's optional brand and logo hints through the shared brand renderer; keep the mark adjacent to the service or account name in the chooser, account list, setup header and chat offer. Missing, unknown or failed images use a generic mark. Do not introduce provider-specific page components or infer branding from action IDs.

Account rows lead with the account name, then service name and a compact textual status. Keep one Add connection action. The account menu uses aligned semantic icons with visible labels. Supporting service descriptions belong in the chooser; avoid repeating them on every saved account.

Setup has one primary action with a visible label. Known `edit` and `test` submit actions may use secondary icon-only buttons with translated tooltips and matching accessible names when they are not primary. Confirmation actions and unknown icon tokens retain visible labels. Open-URL and copy controls keep their descriptive labels beside their glyphs. The Host maps semantic tokens uniformly; a plugin supplies no CSS, arbitrary SVG markup, glyph-library names or icon-only override.

Keep names, field labels, permissions, saved-versus-tested distinctions, destructive consequences, conflicts and recovery visible. Remove repeated instructions before shrinking text or replacing meaningful status with color alone. Icon-only targets retain focus and touch geometry under the shared accessibility rules. Presentation changes never dispatch actions or alter setup ordering and confirmation.

## Copy and States

Sign-in leads with the brand mark, title and brief guidance, then a full-width field and primary action. Language is a quiet footer control, not a heading above the task.

Use concise action labels, including **New**, **Search** and **Goal**. Remove repeated context and optional technical explanations from the resting view; keep consequential permission, deletion, conflict and retry information beside its action. Thinking and Speed reflect actual Model capabilities and distinguish inherited defaults from explicit values.

[Feedback Placement](04-workbench-interaction.md#feedback-placement) gives each failure one recovery owner. Do not repeat the same error in the header, transcript, composer and footer. Preserve drafts and expose the relevant next action.

Loading, empty, error, disabled and expanded states retain alignment. Refreshes and directory navigation retain existing content without flashing. Empty states align to their owning column. Long names and expanded readers keep actions reachable; compactness never removes recorded information or accessibility.

## Implementation Owners

- [Harness theme](../../../frontend/apps/a13n-harness-ui/src/shell/theme.css): tokens, typography, control and overlay treatment.
- [Settings compositions](../../../frontend/apps/a13n-harness-ui/src/shell/settings.tsx): grouped forms and label/value rows.
- [Shared UI and brand](../../../frontend/packages/a13n-ui/README.md): primitives, focus, overlays and identity assets.
- [Review fixtures](../../../dev/harness-ui/review.py): representative conversation and recovery content.

Fix recurring issues at their owner and keep fixtures representative when affected behavior changes.
