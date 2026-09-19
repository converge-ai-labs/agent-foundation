import {
  DesktopIcon,
  TerminalWindowIcon,
  GlobeIcon,
  ArrowsLeftRightIcon,
} from "@phosphor-icons/react";
import { BrandIcon } from "a13n-ui";

const localIcons = {
  direct_local: DesktopIcon,
  http_envd: GlobeIcon,
  websocket_envd: ArrowsLeftRightIcon,
};
export function ProviderIcon({ type }: { type: string }) {
  const LocalIcon = localIcons[type as keyof typeof localIcons];
  return LocalIcon ? (
    <LocalIcon aria-hidden className="size-5 shrink-0 text-muted-foreground" />
  ) : (
    <BrandIcon identity={type} />
  );
}
