export function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
export function fieldSchema(
  value: unknown,
  root: Record<string, unknown>,
): Record<string, unknown> {
  if (!object(value)) return {};
  if (
    typeof value.$ref === "string" &&
    value.$ref.startsWith("#/$defs/") &&
    object(root.$defs)
  ) {
    // A referenced schema carries its own type name as `title`. A field is
    // named by the property that holds it, never by the type behind it.
    const { title: _typeName, ...referenced } = fieldSchema(
      root.$defs[value.$ref.slice(8)],
      root,
    );
    return { ...referenced, ...value };
  }
  if (Array.isArray(value.anyOf))
    return {
      ...value,
      ...fieldSchema(
        value.anyOf.find((item) => object(item) && item.type !== "null"),
        root,
      ),
    };
  return value;
}

/** Object alternatives keep their own fields and defaults, including local references. */
export function objectVariants(schema: Record<string, unknown>) {
  const choices = schema.oneOf ?? schema.anyOf;
  if (!Array.isArray(choices)) return [];
  return choices.flatMap((choice) => {
    const resolved = fieldSchema(choice, schema);
    if (resolved.type !== "object" || !object(resolved.properties)) return [];
    const reference =
      object(choice) && typeof choice.$ref === "string" && object(schema.$defs)
        ? schema.$defs[choice.$ref.slice(8)]
        : undefined;
    return [
      {
        ...resolved,
        $defs: schema.$defs,
        title:
          resolved.title ?? (object(reference) ? reference.title : undefined),
      },
    ];
  });
}

/** A populated draft identifies its alternative without retaining secret values in UI state. */
export function matchingVariant(
  variants: Record<string, unknown>[],
  value: Record<string, unknown>,
) {
  return variants.findIndex(
    (variant) =>
      object(variant.properties) &&
      Object.keys(value).some((key) =>
        Object.hasOwn(variant.properties as Record<string, unknown>, key),
      ),
  );
}
