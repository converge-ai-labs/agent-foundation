# Console shared compositions

`shared/` is the layer between the `a13n-ui` package and the feature screens. It owns the page anatomies described in [the design system](../../../../../spec/frontend/design-system.md): a reader who has used one list, detail page, editor, or dialog can use all of them. Features compose these pieces and keep only their own layout in `features/<area>/<area>.module.css`.

Import from a folder barrel (`shared/page`, `shared/collection`, …), never from a file inside it. If a composition is missing or insufficient, extend the shared one and keep its API small rather than forking it into a feature.

## `shared/page`

The frame of a screen.

- `Page` — list page: optional back link, 22px title with an optional count, one primary action aligned with the title, a one-line description, an optional `toolbar` slot, then the collection. Also hosts the `PageActions` portal. When an `Empty` inside it offers an action, the header actions step aside so the screen offers the primary action once.
- `PageActions` — lets a nested editor present its trigger in the page header without moving its state out of the component that owns it.
- `DetailPage` — detail page: back link, identity `header`, underline `tabs`, and the content column. Pass `rail` to get the two-column layout.
- `DetailHeader` — identity header: avatar or brand tile, name, status pill, an inline edit affordance, the key chip with copy, the one-line description, and the primary actions at the right.
- `DetailLayout` — the content column with an optional 256px sticky rail. Use it directly when an editor owns the `<form>` element that must wrap both the content and the rail.
- `Rail`, `RailSection`, `RailRow`, `RailNote` — the quiet summary groups inside the rail: an 11px uppercase heading, then label and value rows.
- `useTabParam` — reads and writes the active tab through `?tab=`; the first tab is the default and never appears in the URL.
- `Section` — a titled group on a surface: 15px title, 12.5px description, optional actions at the right, and the section body. The region is labelled by its heading. Sections never draw an outline; the editor separates them with 36px of whitespace.
- `Panel` — the side panel that inspects one row without leaving the collection: a header with the identity and its actions, an optional tab strip, a scrollable body, and a drag handle at the left edge. `inline` places the same anatomy in a layout slot its owner sizes, so a panel that reads beside a transcript never covers it; pass `width` and `onWidthChange` to own that width.
- `SaveBar` — the floating bar for draft editors: a pulse dot, what changed, the consequence of saving, an optional note field, Discard, and Save. It is a `type="submit"` control, so it belongs inside the editor's form.

## `shared/collection`

Everything that renders many resources.

- `ResourceTable` — the one table anatomy. Columns declare `label`, `tone`, and `render`; the first column is the identity. `onRowActivate` makes rows clickable and keyboard-activatable; `rowMenu` adds the trailing overflow menu column, which stays visible on touch devices.
- `ResourceIdentity` — the identity cell: a 32px tile, the name at 13.5px medium, one line of secondary text, and the reference popover for identifiers. Re-exported here because it is a table concern as much as an identity one.
- `Toolbar` — search input at 300px with a leading icon, then filters, then secondary actions at the right. `searchLabel` names the field; `searchPlaceholder` adds the hint when the field accepts something more specific.
- `CollectionFooter` — result count at the left, pagination at the right.
- `Pagination` + `useCursor` — server-side Previous/Next over an opaque cursor.
- `Empty` — centred empty state on a surface: icon tile, title, one line, and the primary action.
- `ListRows`, `ListRow`, `ListRowsEmpty` — surface rows for lists that live inside a section or an editor: icon tile, name plus secondary line, an optional trailing control, and row actions.
- `ResourcePicker` — the popover picker that attaches workspace resources to something: search, checkbox rows with selected items first, and a footer that counts the selection and links to the managing page.

## `shared/feedback`

State that the user has to read.

- `StatePill` — maps a domain state to a semantic `StatusPill` and translates it through `state.<value>`. This is the only status affordance in the console; feature pills such as the bot `SetupPill` delegate to it rather than picking their own hue.
- `ErrorNotice` — inline, beside the form or collection that owns the failure, with an optional retry.
- `ErrorToast` — for isolated action results; it self-dismisses with its owner.
- `ErrorPage` — reserved for views that cannot function at all.
- `Loading` / `InlineLoading` — skeletons that preserve the destination's layout (`page`, `table`, `cards`, `form`, `detail`, `list`, `code`). Spinners (`status`) are reserved for gates and explicit actions.
- `Timestamp` — absolute or relative time with the full value in the title.

## `shared/forms`

Fields and form chrome that several features share.

- `FormActions` — the Cancel and submit pair at the end of a form. `variant="outline"` keeps a secondary submit row, such as replacing credentials, from competing with the screen's one filled primary.
- `TextAreaField` — long-form text on the soft surface textarea; `code` switches to the monospace family, `maxLength` and `disabled` pass through, and read-only mode renders a `ReadOnlyField`.
- `JsonView`, `CodeBlock` — bounded, scrollable code surfaces.
- `SchemaFields`, `withSchemaValues` — fields generated from a JSON schema. A field is named by the property that holds it; a referenced schema's type name is never used as a label.
- `HeaderFields`, `serializeHeaders` — extra HTTP header rows.
- `CredentialEditor` — the replace-or-remove affordance for stored secrets.
- `FormSection` — hairline-separated groups inside a single form, with an optional `actions` slot beside the group heading. `divider={false}` separates a group by whitespace alone.
- `DateTimeField`, `ImagePicker`, `FileUpload` — the non-text controls.
- `ProviderTypeField`, `ProviderEnabled`, `ProviderKeyLink` — provider editors.
- `useSuggestedName` — a name that follows selections until the user edits it.
- `validation` — the shared Ajv instance and the console's validators.

## `shared/dialogs`

Overlays and the state behind them.

- `Confirm` — destructive confirmation: it names the resource and labels the action with its verb.
- `ConflictNotice` — concurrency recovery: a saved resource moved underneath the draft (412), or a create could not be confirmed. One explanation and the two ways out — reconcile with the server, or continue with the draft.
- `useResourceRows` / `useResourceEditorState` — the pair that opens a row's editor as a modal and returns focus to the row that opened it.
- `ResourceModalTitle` — a name, an optional `ResourceKeyChip`, and the reference popover, for editor titles.
- `BrandTitle` — brand mark plus title, used by the second step of a catalog-first creation flow.
- `CatalogTiles` / `CatalogTile` — the tile grid for a small, fixed catalog.
- `DirectoryList` / `DirectoryGroup` / `DirectoryRow` / `DirectoryEmpty` — the searchable, grouped list for a large catalog. A `disabled` row stays visible and legible but cannot be chosen.
- `CatalogStep` — the chosen step: a way back to the catalog, then the form.

## `shared/identity`

How a resource presents itself.

- `IconTile` — the 32 / 36 / 44px surface tile that frames an icon, avatar, or brand mark.
- `avatarColor`, `nameInitials` — the fallback identity for a resource with no image: a hue hashed from its identifier and initials from the first and last word of its name.
- `ResourceIdentity` — name, tile, and secondary line; see `shared/collection`.
- `ResourceReference` — the hash affordance that reveals the ID, key, and any related identifiers, each with a copy button.
- `ResourceKeyChip` — the key chip beside a detail-page title.
- `ResourceKeyField` — the URL key input with its pattern and warning.
- `CopyButton`, `Identifier`, `CopyableId`, `CopyableResourceKey` — copyable identifiers; identifiers are sans-serif, 12px, and muted.
- `ProviderIcon` — the brand or built-in mark for a provider type.
- `ScopeBadge` — workspace versus organization ownership.
- `ResourceEditorButton` — the create-or-edit trigger for a resource modal.

## `shared/` root

Infrastructure with no visual surface: `api.ts` (typed request helpers and pagination), `idempotency.ts`, `download.ts`, `paths.ts`, `time.ts`, `local-date-time.ts`, `markdown.tsx`, `authorization-link.tsx`, `configuration-summary.tsx`, and `shared.module.css` — layout utilities (`stack`, `form`, `twoColumns`, `filters`, `cardGrid`, `card`, `muted`) that feature screens reuse.

## Page anatomies

**List page.** `Page` (back link, title and count, primary action, description) → `Toolbar` (search, filters, secondary actions) → `ResourceTable` with `ResourceIdentity` in the first column, `StatePill` for status, and `rowMenu` for row actions → `CollectionFooter` with `Pagination`. `Empty` replaces the table when the collection is empty; a search with no matches keeps the toolbar.

**Detail page.** `DetailPage` (back link, `DetailHeader`, underline tabs bound to `useTabParam`) → `Section`s separated by 36px, optionally beside a `RailSection` stack. Sections that list items use `ListRows`; sections that edit values use fields.

**Editor with drafts.** A `<form>` wrapping `DetailLayout` (content plus rail) and `SaveBar`. Edits accumulate in a draft hook; the save bar states what saving will publish and offers Discard.

**Dialog.** `ModalFrame` at `md` (520px) or `lg` (640px). Creation starts with `CatalogTiles` or `DirectoryList`, then `CatalogStep` with a `BrandTitle` for the chosen brand, `FormActions` in the sticky footer, and `ErrorNotice` beside the fields that failed.

**Settings page.** The contextual settings layout, then `SettingsSection` and `SettingsRow` from `a13n-ui`: groups on a surface, hairlines between rows, the control at the right. Groups are separated by whitespace, never by a hairline, and a screen that reads as settings uses this anatomy even inside a detail tab.

Shared switches that live in `a13n-ui` rather than here: `SegmentedControl` for an in-place view or mode change (skills preview, environment Fields/JSON, trace payloads, diff layout, dialog modes) — the console never re-implements it locally.
