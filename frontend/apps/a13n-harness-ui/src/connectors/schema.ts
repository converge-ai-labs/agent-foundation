export type Json =
  null | boolean | number | string | Json[] | { [key: string]: Json };
export type FormSchema = {
  type?: string;
  title?: string;
  description?: string;
  $ref?: string;
  $defs?: Record<string, FormSchema>;
  properties?: Record<string, FormSchema>;
  required?: string[];
  items?: FormSchema;
  enum?: Json[];
  anyOf?: FormSchema[];
  default?: Json;
  writeOnly?: boolean;
  minimum?: number;
  maximum?: number;
  minLength?: number;
  maxLength?: number;
  format?: string;
};

export function resolveField(schema: FormSchema, root: FormSchema): FormSchema {
  if (!schema.$ref) return schema;
  if (!schema.$ref.startsWith("#/$defs/"))
    throw new Error("Unsupported form reference");
  const resolved = root.$defs?.[schema.$ref.slice(8)];
  if (!resolved) throw new Error("Form reference is unavailable");
  return { ...resolved, ...schema, $ref: undefined };
}

export function defaultValue(schema: FormSchema, root = schema): Json {
  const field = resolveField(schema, root);
  if (field.writeOnly) return "";
  if (field.default !== undefined) return field.default;
  if (field.anyOf?.some((item) => item.type === "null")) return null;
  if (field.type === "object")
    return Object.fromEntries(
      Object.entries(field.properties ?? {}).map(([key, value]) => [
        key,
        defaultValue(value, root),
      ]),
    );
  if (field.type === "array") return [];
  if (field.type === "boolean") return false;
  if (field.type === "integer" || field.type === "number") return 0;
  return field.enum?.[0] ?? "";
}

export function clearSecrets(
  schema: FormSchema,
  value: Json,
  root = schema,
): Json {
  const field = resolveField(schema, root);
  if (field.writeOnly) return "";
  if (field.anyOf) {
    const nonnull = field.anyOf.find((item) => item.type !== "null");
    return value === null || !nonnull
      ? value
      : clearSecrets(nonnull, value, root);
  }
  if (Array.isArray(value))
    return value.map((item) => clearSecrets(field.items ?? {}, item, root));
  if (value && typeof value === "object")
    return Object.fromEntries(
      Object.entries(value).map(([key, item]) => [
        key,
        clearSecrets(field.properties?.[key] ?? {}, item, root),
      ]),
    );
  return value;
}
