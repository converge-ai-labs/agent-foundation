import { CubeIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { resolveBrand } from "./brands";

export interface BrandIconProps {
  identity?: string;
  alias?: string;
  endpoint?: string;
  logo?: string | null;
}

export function BrandIcon({ identity, alias, endpoint, logo }: BrandIconProps) {
  const brand = resolveBrand({ identity, alias, endpoint });
  const [failed, setFailed] = useState<readonly string[]>([]);
  const src =
    brand && !failed.includes(brand.icon) ? brand.icon : safeLogo(logo);
  if (!src || failed.includes(src))
    return (
      <CubeIcon aria-hidden className="size-5 shrink-0 text-muted-foreground" />
    );
  const branded = src === brand?.icon;
  const darkIcon = branded ? brand.darkIcon : undefined;
  const props = {
    alt: "",
    width: 20,
    height: 20,
    decoding: "async" as const,
    referrerPolicy: "no-referrer" as const,
    onError: () => setFailed((previous) => [...previous, src]),
  };
  return (
    <span className="size-5 shrink-0">
      <img
        {...props}
        src={src}
        className={`size-5 object-contain ${darkIcon ? "dark:hidden" : branded && brand.invertInDark ? "dark:invert" : ""}`}
      />
      {darkIcon && (
        <img
          {...props}
          src={darkIcon}
          className="hidden size-5 object-contain dark:block"
        />
      )}
    </span>
  );
}

function safeLogo(value?: string | null): string | undefined {
  if (!value) return undefined;
  try {
    const url = new URL(value);
    return url.protocol === "https:" && !url.username && !url.password
      ? url.href
      : undefined;
  } catch {
    return undefined;
  }
}
