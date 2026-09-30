import type { CSSProperties } from "react";

/**
 * How a resource without an image presents itself: one stable hue per
 * identity, and initials that tell similar names apart.
 */
const colors = [
  "#4f46e5",
  "#7c3aed",
  "#a21caf",
  "#be185d",
  "#be123c",
  "#c2410c",
  "#b45309",
  "#4d7c0f",
  "#047857",
  "#0e7490",
  "#0369a1",
  "#1d4ed8",
];

/**
 * The same identity always reads the same hue, before and after a rename. The
 * hue tints the tile rather than filling it, so a list of identities stays
 * calmer than the states beside them; mixing toward the text colour darkens
 * the initials in light mode and lightens them in dark mode.
 */
export function avatarTint(seed: string): CSSProperties {
  let hash = 2166136261;
  for (const character of seed)
    hash = Math.imul(hash ^ character.codePointAt(0)!, 16777619) >>> 0;
  const hue = colors[hash % colors.length]!;
  return {
    backgroundColor: `color-mix(in srgb, ${hue} 14%, var(--a13n-canvas))`,
    color: `color-mix(in srgb, ${hue} 78%, var(--a13n-text))`,
    boxShadow: `inset 0 0 0 1px color-mix(in srgb, ${hue} 16%, transparent)`,
  };
}

/**
 * Initials from the first and last word, so "Local Reviewer" reads "LR" rather
 * than repeating the shared prefix of every seeded name. Mixed-script names
 * such as "Local Runner / 演示成员" keep to the script they start with.
 */
export function nameInitials(name: string, max: 1 | 2 = 2): string {
  const letters = name
    .trim()
    .split(/[\s/|·,]+/)
    .flatMap((word) => {
      const letter = word.match(/[\p{L}\p{N}]/u)?.[0];
      return letter ? [letter.toLocaleUpperCase()] : [];
    });
  if (!letters.length) return "";
  const latin = (letter: string) => /[A-Za-z0-9]/.test(letter);
  const sameScript = letters.filter(
    (letter) => latin(letter) === latin(letters[0]!),
  );
  if (max === 1 || sameScript.length === 1) return sameScript[0]!;
  return `${sameScript[0]}${sameScript.at(-1)}`;
}
