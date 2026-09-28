/** A model key: lowercase letters, digits, `-` and `.`. */
export const keyPattern = "[a-z0-9][a-z0-9.\\-]{0,127}";

/**
 * The key the Service gives a model created without one:
 * `{provider type}-{upstream name}`, lowercased, with every run of characters
 * a key cannot hold replaced by `-`.
 */
export function defaultModelKey(
  providerType: string | undefined,
  modelName: string,
): string | undefined {
  if (!providerType || !modelName.trim()) return undefined;
  return `${providerType}-${modelName}`
    .toLowerCase()
    .replace(/[^a-z0-9.-]+/g, "-")
    .replace(/^[.-]+|[.-]+$/g, "")
    .slice(0, 128)
    .replace(/[.-]+$/, "");
}
