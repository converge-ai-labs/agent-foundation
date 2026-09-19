/** Provider definitions carry JSON Schema; these read it without trusting it. */
export function schemaProperties(schema?: Record<string, unknown> | null) {
  const properties = schema?.properties;
  return properties !== null &&
    typeof properties === "object" &&
    !Array.isArray(properties)
    ? (properties as Record<string, unknown>)
    : {};
}

export function hasFields(schema?: Record<string, unknown> | null) {
  return Object.keys(schemaProperties(schema)).length > 0;
}

/**
 * A connect step asks for what the service cannot run without, and keeps the
 * rest behind "Advanced settings": required properties are primary, everything
 * else carries a default the provider is happy with.
 */
export function splitConfigurationSchema(
  schema?: Record<string, unknown> | null,
) {
  const properties = schemaProperties(schema);
  const required = Array.isArray(schema?.required)
    ? (schema.required as unknown[]).filter(
        (key): key is string => typeof key === "string",
      )
    : [];
  const pick = (keep: (key: string) => boolean) => ({
    ...schema,
    properties: Object.fromEntries(
      Object.entries(properties).filter(([key]) => keep(key)),
    ),
    required: required.filter(keep),
  });
  return {
    primary: pick((key) => required.includes(key)),
    advanced: pick((key) => !required.includes(key)),
  };
}

/** Whether an advanced value was moved away from what the schema defaults to. */
export function advancedConfigured(
  advanced: Record<string, unknown>,
  configuration: Record<string, unknown>,
) {
  return Object.entries(schemaProperties(advanced)).some(([key, property]) => {
    if (!Object.hasOwn(configuration, key)) return false;
    const field = property as Record<string, unknown>;
    return configuration[key] !== field.default;
  });
}
