/** Presets come from the Service so image policy has one owner. */
export type DockerPreset = "standard" | "desktop";
export type DockerMode = DockerPreset | "custom";
type Recipe = Record<string, unknown>;
export type DockerPresets = Record<DockerPreset, Recipe>;
const resources = new Set(["cpus", "memory_gb", "mounts", "environment"]);

export function dockerPresets(schema?: Recipe): DockerPresets | undefined {
  const presets = schema?.["x-environment-presets"] as
    DockerPresets | undefined;
  return presets?.standard && presets?.desktop ? presets : undefined;
}

export function dockerMode(
  value: Recipe,
  schema: Recipe,
  presets: DockerPresets,
): DockerMode {
  const properties = schema.properties as Record<string, { default?: unknown }>;
  for (const mode of ["standard", "desktop"] as const) {
    const preset = presets[mode];
    const matches = Object.entries(preset).every(
      ([key, expected]) =>
        (value[key] ?? properties[key]?.default) === expected,
    );
    const hasCustomSettings = Object.entries(value).some(
      ([key, current]) =>
        !resources.has(key) &&
        !(key in preset) &&
        JSON.stringify(current) !== JSON.stringify(properties[key]?.default),
    );
    if (matches && !hasCustomSettings) return mode;
  }
  return "custom";
}

export function applyDockerPreset(value: Recipe, preset: Recipe): Recipe {
  // Built-in modes use default advanced settings; resource choices survive.
  return {
    ...Object.fromEntries(
      Object.entries(value).filter(([key]) => resources.has(key)),
    ),
    ...preset,
  };
}
