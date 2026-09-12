import { BrandIcon, brands, resolveBrand } from "a13n-ui";

const identities = Object.keys(brands).sort((a, b) => b.length - a.length);

/** Prefer the model family over the gateway hosting it. */
export function modelBrand(
  upstream: string,
  provider?: string,
): string | undefined {
  const parts = upstream.trim().toLowerCase().split("/").reverse();
  for (const part of parts) {
    if (resolveBrand({ identity: part, alias: part })) return part;
    const family = identities.find(
      (identity) =>
        part.startsWith(identity) &&
        /^[\d._:-]/.test(part.slice(identity.length)),
    );
    if (family) return family;
    if (/^(gpt[-\d]|o[134](?:[-.]|$))/.test(part)) return "openai";
  }
  return provider;
}

export function ModelIcon({
  upstream,
  provider,
}: {
  upstream: string;
  provider?: string;
}) {
  const identity = modelBrand(upstream, provider);
  return <BrandIcon identity={identity} alias={identity} size={32} />;
}
