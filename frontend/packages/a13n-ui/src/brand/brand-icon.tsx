import { CubeIcon } from "@phosphor-icons/react";
import { useState } from "react";
import { resolveBrand, type Brand } from "./brands";

type IconSource = { url: string; brand?: Brand };

export interface BrandIconProps {
  identity?: string;
  alias?: string;
  endpoint?: string;
  logo?: string | null;
  fallbackIdentity?: string;
  size?: number;
}

export function BrandIcon({
  identity,
  alias,
  endpoint,
  logo,
  fallbackIdentity,
  size = 20,
}: BrandIconProps) {
  const brand = resolveBrand({ identity, alias, endpoint });
  const fallbackBrand = fallbackIdentity
    ? resolveBrand({ identity: fallbackIdentity })
    : undefined;
  const [failed, setFailed] = useState<readonly string[]>([]);
  const providerLogo = safeLogo(logo);
  const sources: (IconSource | undefined)[] = [
    brand && { url: brand.icon, brand },
    providerLogo ? { url: providerLogo } : undefined,
    fallbackBrand && { url: fallbackBrand.icon, brand: fallbackBrand },
  ];
  const source = sources.find((item) => item && !failed.includes(item.url));
  if (!source)
    return (
      <CubeIcon
        aria-hidden
        size={size}
        className="shrink-0 text-muted-foreground"
      />
    );
  const { url: src, brand: activeBrand } = source;
  const darkIcon = activeBrand?.darkIcon;
  const props = {
    alt: "",
    width: size,
    height: size,
    decoding: "async" as const,
    referrerPolicy: "no-referrer" as const,
    onError: () => setFailed((previous) => [...previous, src]),
  };
  return (
    <span
      className="inline-flex shrink-0"
      style={{ width: size, height: size }}
    >
      <img
        {...props}
        src={src}
        className={`size-full object-contain ${darkIcon ? "dark:hidden" : activeBrand?.invertInDark ? "dark:invert" : ""}`}
      />
      {darkIcon && (
        <img
          {...props}
          src={darkIcon}
          className="hidden size-full object-contain dark:block"
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
