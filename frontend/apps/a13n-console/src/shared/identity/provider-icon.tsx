import { BrowserIcon, DesktopIcon } from "@phosphor-icons/react";
import { BrandIcon } from "a13n-ui";

export function ProviderIcon({ type }: { type: string }) {
  if (type === "local" || type === "native") {
    const Icon = type === "local" ? DesktopIcon : BrowserIcon;
    return (
      <Icon aria-hidden className="size-5 shrink-0 text-muted-foreground" />
    );
  }
  // The type doubles as an alias so vendor-prefixed types still find their mark.
  return <BrandIcon identity={type} alias={type} />;
}
