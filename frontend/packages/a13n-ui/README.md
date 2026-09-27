# a13n UI

Private shared React components for the frontend workspace, built from the MIT [Coss UI registry](https://github.com/cosscom/coss). The components use Base UI, DayPicker, and Tailwind CSS. See the [design system contract](../../../spec/frontend/design-system.md).

Import components from `a13n-ui` and load `a13n-ui/styles.css` once at the application entry. Use `a13n-root` for application typography. Toggle the `dark` class on the document element so portaled overlays inherit the selected theme.

```tsx
import { Button, FormField, Input } from "a13n-ui";
import "a13n-ui/styles.css";

<FormField label="Name" description="Use a recognizable name.">
  <Input required />
</FormField>
<Button type="submit" loading={saving}>Save changes</Button>
```

## Organization

- `src/components`, `src/hooks`, and `src/lib`: Coss UI registry primitives and their dependencies. [coss-source.json](./coss-source.json) records the upstream revision, imported files, and local adaptations; [LICENSE.coss](./LICENSE.coss) preserves the license.
- `src/patterns`: small compositions shared across application features, including FormField, ChoiceField, ModalFrame, SearchPicker, DisclosureSection, SettingsRow, and SettingsSection.
- `src/styles`: the Tailwind entry, semantic Coss UI light and dark themes, application layout tokens, and bundled fonts.
- `src/brand`: the a13n Logo and Wordmark plus shared product and provider identity mappings.
- `dev`: a standalone interactive showcase with foundations, component states, settings, and collection examples.
- `tests`: interaction checks for shared compositions.

The brand registry maps the complete pinned LobeHub Icons catalog to its jsDelivr CDN rather than vendoring SVG files. Run `pnpm --dir frontend --filter a13n-ui sync:brands` to regenerate it after updating the source versions documented in `src/brand/README.md`.

## Composition

Use the Coss UI component API directly. Button uses `onClick`, `disabled`, `loading`, and `variant`; icon buttons use an icon size and an accessible name. Loading preserves the action label and exposes busy state. Add translated status text to standalone Spinner uses.

FormField associates a label, description, and error with its Input or Textarea. ChoiceField accepts options and controlled `value` / `onValueChange`; use `hideLabel` when another visible label already names the control. For custom composition, use Field and Select parts directly.

Select and SearchPicker choose values; DisclosureSection expands optional configuration. Keep simple fields visible and use a named Button with a plus icon for add actions. See the [control meaning contract](../../../spec/frontend/design-system.md#control-meaning-and-configuration) for layout and interaction rules.

SearchPicker accepts grouped options and controlled `value` / `onValueChange`. It searches labels, descriptions, group names, and keywords. Supply unique option values and group names, translated labels, a placeholder, and an empty message. Selection persists while the search query resets on close. Disabled options cannot activate.

ModalFrame supplies a named dialog, a bounded scroll region, and an optional fixed footer. Use `size="lg"` for complex resource forms. A form-owned footer marked `data-a13n-form-actions` stays reachable while scrolling and preserves native submission.

DisclosureSection accepts a title, optional summary, and Collapsible state props. Keep configuration state in the parent so collapsing preserves the draft. Its shared surface encloses both the trigger and expanded controls.

Textarea respects `rows`, caps resizing, and scrolls long content internally. Use ScrollArea for bounded content regions; set its height through `className`. Native scrolling elements can use `a13n-scrollbar` for the same thumb styling without changing their semantics. Scrollbar colors and size belong to `src/styles/tokens.css`. Use Input or Textarea `unstyled` only when the enclosing composite supplies the control boundary and visible focus treatment.

SettingsSection uses a muted group surface with its title outside and inset dividers between direct children. Each direct child represents one setting or cohesive block. `variant="plain"` omits the surface and automatic dividers. Set spacing between sections in the parent layout.

SettingsRow pairs explanatory copy with a control and stacks them in narrow containers. Its `controlId` associates the visible label and produces a `<controlId>-description` ID for callers to connect with `aria-describedby`.

Applications own translations, navigation, persistence, data fetching, and domain state. Keep those concerns out of the shared package. Prefer Tailwind semantic utilities and `cn` for composition; preserve CSS Modules only where they clarify application-specific layouts.

## Registry updates

`coss-source.json` records the imported registry revision and local changes. Review upstream diffs before updating imported sources. Keep application-specific behavior in `src/patterns`, and the Coss semantic theme in `src/styles/theme.css`.

## License distribution

Every Vite consumer uses `cossLicense()` from `a13n-ui/vite`. It copies the canonical `LICENSE.coss` to `assets/LICENSE.coss` and preserves the complete notice in JavaScript and CSS output through minification. The application asset trees carry this notice into Python wheels, source distributions, and Docker images; the documentation site also bundles the same license with its theme. No product branding or visible attribution is added.

## Development

Run `pnpm --dir frontend --filter a13n-ui dev` for the showcase. The package scripts `check`, `test`, and `build` run type checking, interaction tests, and the showcase build respectively. The complete frontend gate is `make frontend-check-all`.
