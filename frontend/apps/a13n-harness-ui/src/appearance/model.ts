export const sizeRules = {
  navigation: { default: 13, min: 12, max: 16 },
  interface: { default: 14, min: 12, max: 16 },
  conversation: { default: 15, min: 14, max: 20 },
  composer: { default: 16, min: 14, max: 20 },
  code: { default: 12, min: 11, max: 18 },
  terminal: { default: 13, min: 11, max: 20 },
} as const;
export type SizeRegion = keyof typeof sizeRules;
export type Theme = "light" | "dark" | "system";
export type Appearance = {
  version: 1;
  theme: Theme;
  sizes: Record<SizeRegion, number>;
};
export const regions = Object.keys(sizeRules) as SizeRegion[];
export function defaultAppearance(): Appearance {
  return {
    version: 1,
    theme: "light",
    sizes: Object.fromEntries(
      regions.map((key) => [key, sizeRules[key].default]),
    ) as Appearance["sizes"],
  };
}
export function validSize(region: SizeRegion, value: unknown): value is number {
  const { min, max } = sizeRules[region];
  return (
    typeof value === "number" &&
    Number.isInteger(value) &&
    value >= min &&
    value <= max
  );
}
export function parseAppearance(raw: string | null): Appearance {
  const result = defaultAppearance();
  if (raw === null) return result;
  let value: unknown;
  try {
    value = JSON.parse(raw);
  } catch {
    return result;
  }
  if (
    !value ||
    typeof value !== "object" ||
    !("version" in value) ||
    value.version !== 1
  )
    return result;
  if (
    "theme" in value &&
    (value.theme === "light" ||
      value.theme === "dark" ||
      value.theme === "system")
  )
    result.theme = value.theme;
  if ("sizes" in value && value.sizes && typeof value.sizes === "object") {
    for (const region of regions) {
      const size = (value.sizes as Record<string, unknown>)[region];
      if (validSize(region, size)) result.sizes[region] = size;
    }
  }
  return result;
}
