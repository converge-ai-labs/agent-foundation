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

/** The same identity always reads the same colour, before and after a rename. */
export function avatarColor(seed: string): string {
  let hash = 2166136261;
  for (const character of seed)
    hash = Math.imul(hash ^ character.codePointAt(0)!, 16777619) >>> 0;
  return colors[hash % colors.length]!;
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
