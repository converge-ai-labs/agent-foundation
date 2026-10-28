import { brands, resolveBrand } from "./brands";

const identities = Object.keys(brands).sort((a, b) => b.length - a.length);

/** Prefer the model family over the gateway hosting it. */
export function modelBrand(
  upstream: string,
  provider?: string,
): string | undefined {
  const parts = upstream.trim().toLowerCase().split(/[/.]/).reverse();
  for (const part of parts) {
    if (resolveBrand({ identity: part, alias: part })) return part;
    const family = identities.find(
      (identity) =>
        part.startsWith(identity) &&
        /^[\d._:-]/.test(part.slice(identity.length)),
    );
    if (family) return family;
    if (/^glm[-\d]/.test(part)) return "zhipu";
    if (/^(doubao[-.]|seed[-\d])/.test(part)) return "doubao";
    if (/^(gpt[-\d]|o[134](?:[-.]|$))/.test(part)) return "openai";
    if (/^llama(?:[-\d]|$)/.test(part)) return "meta";
  }
  return provider;
}
