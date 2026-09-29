import {
  AppWindow,
  Compass,
  Cube,
  Stack,
  TerminalWindow,
} from "@phosphor-icons/react/dist/ssr";
import type { Icon } from "@phosphor-icons/react";

/** Product icons, referenced by name from `meta.json` files. */
const productIcons: Record<string, Icon> = {
  overview: Compass,
  service: Stack,
  "harness-ui": AppWindow,
  harness: Cube,
  environments: TerminalWindow,
};

export function icon(name: string | undefined) {
  const Component = name ? productIcons[name] : undefined;
  return Component ? <Component weight="duotone" /> : undefined;
}
