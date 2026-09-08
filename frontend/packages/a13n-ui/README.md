# a13n UI

Private shared React components and design tokens for the frontend workspace. Uses Radix primitives, unstyled cmdk search behavior, CSS Modules, and CSS custom properties. See the [design system contract](../../../spec/frontend/design-system.md).

Import components from `a13n-ui` and load `a13n-ui/styles.css` once at the application entry. Add `a13n-root` to the application container for base typography. Set `data-a13n-theme="light"` or `"dark"` on the document element so portaled overlays inherit the same tokens. Light is the default.

```tsx
import { Button } from "a13n-ui";
import "a13n-ui/styles.css";

<Button loading={saving} loadingLabel="Saving…">Save changes</Button>
```

Pass `loading` consistently as a boolean to reserve the spinner slot, and provide a translated `loadingLabel` when the label changes. Both labels participate in layout, preserving dimensions. Icon-only buttons need an accessible name. Spinner is decorative; its owner supplies loading text or a status announcement. Components accept display strings; applications own translations and business state.

## Composition

Use `Select` for a standalone control, with its required `label` serving as the accessible name. Use `SelectField` for a visible form label, hint, and error. Existing labeled Select callers must migrate to SelectField. Both share the same interaction implementation; `size="sm"` and `variant="ghost"` support settings and inline properties.

```tsx
<SettingsSection title="Appearance">
  <SettingsRow label="Theme" controlId="theme" description="Choose your preferred appearance.">
    <Select
      id="theme"
      aria-describedby="theme-description"
      label="Theme"
      placeholder="Choose theme"
      size="sm"
      value={theme}
      onValueChange={setTheme}
      options={[{ value: "light", label: "Light" }, { value: "dark", label: "Dark" }]}
    />
  </SettingsRow>
</SettingsSection>
```

`Picker` takes grouped options and controlled `value` / `onValueChange`. It searches labels, descriptions, group names, and `keywords`. Provide unique option values and group names, a translated search label, placeholder, and empty message. Describe disabled reasons in option descriptions. The trigger announces its current value; search resets when the popover closes.

`Menu` takes groups of actions with `onSelect` callbacks. `CommandPalette` takes grouped search options and a controlled open state, focusing search on open. `Kbd` only displays a shortcut; applications register the actual keyboard action. The showcase demonstrates a command palette opened by a button or Cmd/Ctrl+K. Keep shortcut policy, domain commands, and navigation in the application.

`SettingsSection` and `SettingsRow` lay out related preferences. Give the row a `controlId` to connect its label, and pass `<controlId>-description` as the control's `aria-describedby` when using a description. `Switch labelHidden` fits rows without duplicating the visible label. Disabled explanations remain visible. `Tabs` owns keyboard tab selection; `EmptyState` pairs an explanation with an optional recovery action.

## Development showcase

From the repository root:

```bash
make frontend-sync
pnpm --dir frontend --filter a13n-ui dev
make frontend-check-all
```

Open `http://127.0.0.1:5175/#settings`. Use the navigation to explore foundations, components, settings, and a searchable collection with a detail pane. The independent showcase in `dev/` exercises foundations, control states, validation, icon options, keyboard interactions and overlays in English and Simplified Chinese, light and dark themes, and a narrow preview. It is not exported by the package or routed through Console. `build:showcase` writes disposable assets to `dist/showcase`; package checks include type checking, interaction tests, and the showcase build.

The typed exports in `src/index.ts` own the component API; `src/styles/tokens.css` owns concrete design values. Add shared primitives only for established UI needs, and keep product compositions in applications.
