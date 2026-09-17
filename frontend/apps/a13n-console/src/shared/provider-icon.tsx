import {
  DesktopIcon,
  TerminalWindowIcon,
  GlobeIcon,
  ArrowsLeftRightIcon,
} from "@phosphor-icons/react";
import { BrandIcon } from "a13n-ui";

const localIcons = {
  "a13n.direct-local": DesktopIcon,
  "a13n.http-envd": GlobeIcon,
  "a13n.websocket-envd": ArrowsLeftRightIcon,
};
export function ProviderIcon({ type }: { type: string }) {
  const LocalIcon = localIcons[type as keyof typeof localIcons];
  return LocalIcon ? (
    <LocalIcon aria-hidden className="size-5 shrink-0 text-muted-foreground" />
  ) : (
    <BrandIcon identity={type} />
  );
}
