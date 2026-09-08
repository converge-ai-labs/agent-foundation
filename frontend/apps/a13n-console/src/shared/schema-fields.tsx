import { useState } from "react";
import { Input, SelectField, Checkbox } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { jsonValue } from "./validation";
import styles from "./shared.module.css";

function object(value: unknown): value is Record<string, unknown> {
  return value !== null && typeof value === "object" && !Array.isArray(value);
}
function fieldSchema(
  value: unknown,
  root: Record<string, unknown>,
): Record<string, unknown> {
  if (!object(value)) return {};
  if (
    typeof value.$ref === "string" &&
    value.$ref.startsWith("#/$defs/") &&
    object(root.$defs)
  )
    return { ...fieldSchema(root.$defs[value.$ref.slice(8)], root), ...value };
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
/** Provider-owned JSON Schema drives ordinary fields; complex values retain a validated JSON input. */
export function SchemaFields({
  schema,
  value,
  onChange,
  secret = false,
}: {
  schema: Record<string, unknown>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  secret?: boolean;
}) {
  const { t } = useTranslation();
  const properties = object(schema.properties) ? schema.properties : {};
  function change(key: string, next: unknown) {
    const draft = { ...value };
    if (next === undefined) delete draft[key];
    else draft[key] = next;
    onChange(draft);
  }
  return (
    <div className={styles.stack}>
      {Object.entries(properties).map(([key, definition]) => {
        const field = fieldSchema(definition, schema),
          label = t(typeof field.title === "string" ? field.title : key),
          required =
            Array.isArray(schema.required) && schema.required.includes(key);
        const description =
          typeof field.description === "string"
            ? t(field.description)
            : undefined;
        const current = value[key] ?? field.default;
        if (
          Array.isArray(field.enum) &&
          field.enum.every((item) => typeof item === "string")
        )
          return (
            <SelectField
              key={key}
              label={label}
              hint={description}
              placeholder={t("Select…")}
              value={typeof current === "string" ? current : undefined}
              onValueChange={(next) => change(key, next)}
              options={field.enum.map((item) => ({
                value: String(item),
                label: String(item),
              }))}
              required={required}
            />
          );
        if (field.type === "boolean")
          return (
            <Checkbox
              key={key}
              label={label}
              checked={current === true}
              onCheckedChange={(next) => change(key, next === true)}
            />
          );
        if (field.contentMediaType === "application/x-pem-file")
          return (
            <label key={key} className={styles.field}>
              <span>{label}</span>
              <textarea
                autoComplete="off"
                spellCheck={false}
                rows={6}
                value={typeof current === "string" ? current : ""}
                onChange={(event) =>
                  change(key, event.target.value || undefined)
                }
                required={required}
                aria-label={label}
              />
            </label>
          );
        if (["string", "number", "integer"].includes(String(field.type)))
          return (
            <Input
              key={key}
              label={label}
              hint={description}
              type={
                secret || field.format === "password"
                  ? "password"
                  : field.type === "string"
                    ? "text"
                    : "number"
              }
              autoComplete={secret ? "off" : undefined}
              value={
                typeof current === "string" || typeof current === "number"
                  ? current
                  : ""
              }
              onChange={(event) =>
                change(
                  key,
                  event.target.value === ""
                    ? undefined
                    : field.type === "string"
                      ? event.target.value
                      : Number(event.target.value),
                )
              }
              min={
                typeof field.minimum === "number" ? field.minimum : undefined
              }
              max={
                typeof field.maximum === "number" ? field.maximum : undefined
              }
              step={field.type === "integer" ? 1 : "any"}
              required={required}
            />
          );
        return (
          <JsonField
            key={key}
            label={label}
            value={current}
            onChange={(next) => change(key, next)}
          />
        );
      })}
    </div>
  );
}
function JsonField({
  label,
  value,
  onChange,
}: {
  label: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const [text, setText] = useState(
      value === undefined ? "" : JSON.stringify(value, null, 2),
    ),
    [error, setError] = useState("");
  return (
    <label className={styles.field}>
      <span>{label}</span>
      <textarea
        className={styles.code}
        rows={4}
        value={text}
        aria-invalid={!!error}
        onChange={(event) => {
          setText(event.target.value);
          try {
            const value = event.target.value.trim()
              ? jsonValue(event.target.value)
              : undefined;
            event.target.setCustomValidity("");
            setError("");
            onChange(value);
          } catch (error) {
            const message =
              error instanceof Error ? error.message : "Invalid JSON";
            event.target.setCustomValidity(message);
            setError(message);
          }
        }}
      />
      {error && <small role="alert">{error}</small>}
    </label>
  );
}
