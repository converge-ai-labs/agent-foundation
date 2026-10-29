# Console Design Guidelines

## Scope

This document owns Console presentation. The [shared design system](design-system.md) owns common primitives and accessibility; [Console](console.md) owns behavior and authority. The [composition catalog](../../frontend/apps/a13n-console/src/shared/README.md) documents implementation APIs. Harness has its own design contract.

Console is a quiet management and execution interface: names, status and the next action lead; identifiers and diagnostics remain secondary. Resource screens share list, detail, editor and settings anatomies.

## Surfaces and Type

Use cool neutrals: grouping fill about 3–4%, hairlines about 6–8%. Radii: groups/dialogs/empty states 12px, popovers/menus 10px, rows/controls/tiles 8px, chips 6px, pills fully rounded. Configuration groups use canvas and one subtle outline; nested content adds no frame. Dark mode preserves these roles.

System sans-serif uses 13px interface text and 14px prose at 1.6 line height. Regular weight is 400; interface emphasis is 500, with slightly tightened title tracking. Use only the needed roles, without fractional local sizes.

| Element                    | Size / weight | Color     |
| -------------------------- | ------------- | --------- |
| Page title                 | 24px / 500    | text      |
| Dialog title               | 17px / 500    | text      |
| Section title              | 15px / 500    | text      |
| Row name                   | 14px / 500    | text      |
| Field label                | 13px / 500    | text      |
| Body, table cells          | 13px / 400    | text      |
| Page and section summaries | 13px / 400    | secondary |
| Help text, metadata        | 12px / 400    | secondary |
| Table headers              | 12px / 500    | secondary |
| Rail and group headings    | 11px / 500    | secondary |
| Identifiers, keys, URLs    | 12px / 400    | secondary |

`StatePill` maps domain states to semantic variants: a 6px dot and 12px medium label, settled states untinted and attention states at 10–12% tint. Tables and identity headers retain pills. Markdown links use signal color, medium weight and persistent underline; hover strengthens it. Interface resource links stay neutral.

## Page Anatomy

| Surface      | Composition                                                                                                                                                                                                                                                 |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| List         | Nested back link → title/count and primary action → one-line summary → search/filter/action toolbar → collection → count/pagination footer. Max width 1280px, top inset 32px, fluid side gutters 20–48px; reserve scrollbar space. Search is at most 300px. |
| Detail       | Back link → identity header with 44px image, name, status, edit, copyable key, summary and actions → underline tabs → content, optionally beside a 256px sticky rail. Max width 1280px; sections have 13px descriptions and 32px separation.                |
| Draft editor | Floating bottom-center save bar: pulse, Unsaved changes, consequence, optional note, Discard and Save. Autosave instead shows status beside its control.                                                                                                    |
| Settings     | Context navigation with search and Back to workspace; centered 1040px column. Aligned headings, outlined groups, left label/explanation, right control and internal hairlines. Headings sit outside groups, separated by whitespace.                        |
| Empty        | Centered 40px icon tile, 15px title, 13px explanation and one primary action; omit its duplicate in the header. No search matches uses a compact message and retains the toolbar.                                                                           |

Narrow pages keep reading width: actions wrap below the title; search and filters shrink or wrap without all becoming full-width. Header/toolbar controls remain 32px with 13px labels and usable touch targets. Skeletons retain these gutters and geometry.

## Collections

Use `ResourceTable` with page scrolling, never a nested fixed-height list. Keep toolbar and pagination outside its thin rounded frame. Header: light neutral fill, 40px height, 12px medium secondary text. Rows: 52–60px, hairline separation and 3–4% hover fill.

Identity leads with a 32px image, 14px medium name and one supporting line. Other cells use 13px; timestamps and identifiers use 12px secondary. Numeric comparisons align right with tabular figures; timestamps align left. Trailing overflow actions remain available on touch. Picker rows replace images with checkboxes and place selected items first.

Search and filters cover the collection, never just the loaded page: query the server or fetch the complete collection if filtering locally. Long collections paginate with count and Previous/Next.

## Forms and Fields

Groups follow identity, connection/storage, behavior and advanced controls. Use a 15px medium heading outside a 12px-radius outline, 16px insets/field gaps and 24px between groups; narrow insets are 14px. One or two related fields need no extra heading. Nested groups add no frame; opening them preserves the draft.

| Field                                     | Layout                                                                                                           |
| ----------------------------------------- | ---------------------------------------------------------------------------------------------------------------- |
| Single text value / short read-only value | Aligned label and control columns; description/validation beneath the control                                    |
| Boolean, choice or short number           | Label and explanation together at left, control centered at right; switches remain beside text on narrow screens |
| Long text / JSON                          | Label above, full writing width; borderless soft surface with bounded internal scroll                            |
| Choice trigger                            | Content width within readable bounds, one trailing chevron; long selected names truncate, menus may be wider     |

Labels are 13px medium; help is 12px secondary. Validation names the correction without discarding input. Optional labels say “(optional)”; required fields have no asterisk. Schema fields use property names, not schema type names. Read-only long content retains its surface; short values have no box; model values retain brand, name and key.

Inputs are 32px (36px below `sm`) with 8px corners. Header/toolbars use 32px controls, section/footer actions 28px, extra-small icon buttons 24px; peers share heights. Compact filters omit the visible label column. Narrow layouts stack when controls cannot fit.

Use primary, outline, ghost and destructive button roles; a subordinate form submit uses outline. Save/Cancel stay right, staged Enabled left, connection feedback above. Menu actions have aligned icons; destructive actions sit last after a separator. Consequential deletion uses confirmation.

Segmented controls switch in-place modes, never navigation: soft track, elevated active segment, medium labels, no outline. Small is 28px/12px; default 32px/13px. Unavailable choices remain disabled rather than hidden.

## Dialogs and Provider Editors

Dialogs are 520px for standard forms, 640px for complex forms and 880px for comparisons; header type is 17px title/13px summary, with 16px field gaps. Keep step height stable, advanced controls disclosed and footer actions reachable. Catalog-first creation uses tiles or a searchable list, then a brand-marked setup step.

Ordinary Console modals dismiss from the topmost backdrop without activating the page or submitting. Pending requests continue independently of dismissal; operation-specific safety constraints remain in [Console behavior](console.md). Entry/exit uses 120ms ease-out opacity, without slide/scale; retain content until exit finishes. Reduced motion removes transitions.

Provider editors group Basic information, Credentials and Configuration, omitting empty groups. Credentials link to their issuing page; Saved/Not configured states expose Replace/Add without destroying saved values on cancellation. Optional non-secret settings disclose in place. Check connection feedback sits above Enabled (left) and Delete/Cancel/Save (right). Delete confirms the resource, shows actionable reference refusals and bounds environment-reference previews. OAuth has separate progress and recovery.

## Conversation Rendering

### Chat

The compact session header contains identity, status, view switch and actions; session ID is copied from the actions menu. Narrow headers put view controls on a second line. The transcript is centered at 760px.

User messages are right-aligned soft bubbles. Agent prose sits beneath a small avatar and model/brand line with override count, opening captured configuration. Work, prose, notices and actions share the avatar's left edge. One quiet tool summary expands compact rows. Working uses the animated a13n mark at the next step; completed name lines show duration. Finished answers offer Copy and Create branch; waiting answers omit those actions.

### Debug

Debug is an execution log without avatars, bubbles or reply prose.

| Region       | Presentation                                                                                                                                                                              |
| ------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Run card     | Soft border, 12px radius and gap. Header: caret, 14px semibold name, status and secondary actions; actions become glyph-only when narrow. Closed cards retain input preview and duration. |
| Run figures  | Named start time to seconds, duration, calls, tokens, cache and cost; monospace values, identifier last                                                                                   |
| Log          | Faintly tinted 64px time gutter, steps and fixed cache/token/duration columns. Below 560px retain time, step and duration.                                                                |
| Agent line   | Name, monospace version, model/brand and override count, opening captured configuration; below 560px retain model and count                                                               |
| Input/output | Expand beneath their row; bound long content with fade and Show all. Output has Markdown/Raw and copy or structured rendering.                                                            |
| Step         | 13px category glyph, 13px medium name, secondary subject, monospace figures. Exceptions replace the glyph; completed steps add no status mark. Tool names may be monospace.               |
| Hierarchy    | Model requests contain emitted records, including replies, under 1px depth guides aligned to the parent glyph. Faint hairlines separate top-level entries.                                |
| Open record  | Selected band aligned beneath its name: 11px labels, elevated code, semantic patch colors, collapsed request summary; raw payloads use named dialogs                                      |

Lifecycle lines name outcome and reason; finished runs end with Run completed. Chat tool rows retain the same names, glyphs, subjects and inspectable detail. Debug inbox and wait answers use cards aligned with runs.

### Configuration, Recovery and Details

Captured configuration reuses the agent editor read-only, with agent/version/overrides above sections and Overridden badges on affected groups. Chat failures use a quiet danger notice with title, explanation and Error details; stopped runs use neutral tone. Earlier failures remain visible; only the latest stopped run offers Resubmit.

Run Details leads with agent/duration/timestamps; identifiers and trace links sit behind References. Usage is a model table on wide dialogs and labeled two-column figures when narrow. Policy, mounts, tools, lineage and raw metadata disclose quietly; attempts remain visible. Environment variables are static pairs with explicit empty states.

### Run Navigation and Composer

With multiple runs, Debug at widths ≥1000px uses a 1440px page and 240px sticky run index. Two-line entries show status, name, input excerpt and monospace cost/calls/duration columns. Sticky metric headings sort either way; Order restores chronology. The scrollbar appears on hover. Narrow layouts use a pinned previous/next/list pill. Earlier runs and Later runs are quiet full-width pagination rows with directional chevrons, also used in the narrow menu.

The floating composer has a borderless editor, left attachment/options and round right Send. Cmd/Ctrl+Enter sends; Enter and Shift+Enter insert newlines. Run options use one column with primary model fields and an instruction switch above collapsed groups. Expanded groups match Agent details; the instruction editor appears only when enabled. Changed options mark the composer with a dot. Keep Restore defaults left and Cancel/Apply right, with independent body scrolling.

## Navigation and Motion

The tinted borderless sidebar has a workspace switcher, 32px rows with 8px corners, 11px medium group labels and bottom account menu. Selected fill and duotone icons identify the active item without changing text weight; expanded groups highlight their selected child. Hover uses 2% foreground in light mode, 3% in dark, weaker than selection and table headers.

Collapsed desktop navigation uses an icon rail with tooltips. Below `lg` (1024px), navigation and contextual settings use a drawer and an always-visible menu control aligned to the page gutter. JavaScript queries match CSS breakpoints with no gap. Content gutters remain fluid; increasing width never forces a table back to stacked rows. Routes, including settings, name themselves in the document title.

Disclosures and carets share 150ms ease-out motion. Buttons use slight press scaling (0.97) with pressed fill; reduced motion keeps only fill. Neither press nor selection moves neighboring content or changes type weight. Dialog motion remains opacity-only.
