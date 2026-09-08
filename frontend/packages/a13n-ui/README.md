# a13n UI

Private shared React components and design tokens for the frontend workspace. Uses Radix primitives, CSS Modules, and CSS custom properties. See the [design system contract](../../../spec/frontend/design-system.md).

Import components from `a13n-ui` and load `a13n-ui/styles.css` once at the application entry. Add `a13n-root` to the application container for base typography. Set `data-a13n-theme="light"` or `"dark"` on the document element so portaled overlays inherit the same tokens. Light is the default.

```tsx
import { Button } from "a13n-ui";
import "a13n-ui/styles.css";

<Button loading={saving} loadingLabel="Saving…">Save changes</Button>
```

Pass `loading` consistently as a boolean to reserve the spinner slot, and provide a translated `loadingLabel` when the label changes. Both labels participate in layout, preserving dimensions. Icon-only buttons need an accessible name. Spinner is decorative; its owner supplies loading text or a status announcement. Components accept display strings; applications own translations and business state.

## Development showcase

From the repository root:

```bash
make frontend-sync
pnpm --dir frontend --filter a13n-ui dev
make frontend-check-all
```

Open `http://127.0.0.1:5175`. The independent showcase in `dev/` exercises foundations, control states, validation, icon options, keyboard interactions and overlays in English and Simplified Chinese, light and dark themes, and a narrow preview. It is not exported by the package or routed through Console. `build:showcase` writes disposable assets to `dist/showcase`; package checks include type checking, interaction tests, and the showcase build.

The typed exports in `src/index.ts` own the component API; `src/styles/tokens.css` owns concrete design values. Add shared primitives only for established UI needs, and keep product compositions in applications.
