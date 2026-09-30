/** Translate human descriptions without changing names, examples, or the wire contract. */
export function translateOpenAPI<T>(
  document: T,
  translations: Record<string, string>,
): T {
  function visit(value: unknown): unknown {
    if (Array.isArray(value)) return value.map(visit);
    if (!value || typeof value !== "object") return value;
    return Object.fromEntries(
      Object.entries(value).map(([key, child]) => {
        if (
          (key === "summary" || key === "description") &&
          typeof child === "string"
        )
          return [key, translations[child] ?? child];
        // Sample request bodies and defaults are data, even when a field is called description.
        if (["example", "examples", "default", "const", "enum"].includes(key))
          return [key, child];
        return [key, visit(child)];
      }),
    );
  }
  return visit(document) as T;
}
