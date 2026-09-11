import {
  Checkbox,
  ChoiceField,
  FormField,
  Input,
  Label,
  Textarea,
} from "a13n-ui";

import { useState } from "react";

import { useTranslation } from "react-i18next";
import styles from "./shared.module.css";
import { jsonValue } from "./validation";

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
/** Apply schema defaults and fixed values to the submitted object as well as the form. */
export function withSchemaValues(
  schema: Record<string, unknown>,
  value: Record<string, unknown>,
) {
  const result = { ...value };
  if (object(schema.properties)) {
    for (const [key, definition] of Object.entries(schema.properties)) {
      const field = fieldSchema(definition, schema);
      if (Object.hasOwn(field, "const")) result[key] = field.const;
      else if (result[key] === undefined && Object.hasOwn(field, "default"))
        result[key] = field.default;
      if (field.type === "object" && object(field.properties)) {
        const nested = withSchemaValues(
          field,
          object(result[key]) ? result[key] : {},
        );
        if (Object.keys(nested).length || Object.hasOwn(result, key))
          result[key] = nested;
      }
    }
  }
  return result;
}

/** Provider-owned JSON Schema drives ordinary fields; complex values retain a validated JSON input. */
export function SchemaFields({
  schema,
  value,
  onChange,
  secret = false,
  descriptions = true,
}: {
  schema: Record<string, unknown>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  secret?: boolean;
  descriptions?: boolean;
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
    <div className={styles.schemaFields}>
      {Object.entries(properties).map(([key, definition]) => {
        const field = fieldSchema(definition, schema),
          label = t(typeof field.title === "string" ? field.title : key),
          required =
            Array.isArray(schema.required) && schema.required.includes(key);
        const description =
          descriptions && typeof field.description === "string"
            ? t(field.description)
            : undefined;
        if (Object.hasOwn(field, "const")) return null;
        const current = value[key] ?? field.default;
        const options = Array.isArray(field.oneOf)
          ? field.oneOf.flatMap((choice) =>
              object(choice) && typeof choice.const === "string"
                ? [
                    {
                      value: choice.const,
                      label:
                        typeof choice.title === "string"
                          ? t(choice.title)
                          : choice.const,
                    },
                  ]
                : [],
            )
          : Array.isArray(field.enum)
            ? field.enum.flatMap((item) =>
                typeof item === "string" ? [{ value: item, label: item }] : [],
              )
            : [];
        if (options.length)
          return (
            <ChoiceField
              key={key}
              placeholder={t("Select…")}
              value={typeof current === "string" ? current : undefined}
              className="min-w-0"
              required={required}
              onValueChange={(next) => change(key, next)}
              label={label}
              options={options}
              description={description}
            />
          );
        if (field.type === "object" && object(field.properties)) {
          if (!Object.keys(field.properties).length) return null;
          return (
            <fieldset key={key} className={styles.stack}>
              <legend>{label}</legend>
              <SchemaFields
                schema={field}
                value={object(current) ? current : {}}
                onChange={(next) => change(key, next)}
                secret={secret}
                descriptions={descriptions}
              />
            </fieldset>
          );
        }
        if (field.type === "boolean")
          return (
            <Label key={key} className="flex items-center gap-2">
              <Checkbox
                checked={current === true}
                onCheckedChange={(next) => change(key, next === true)}
              />
              {label}
            </Label>
          );
        if (field.contentMediaType === "application/x-pem-file")
          return (
            <label key={key} className={styles.field}>
              <span>{label}</span>
              <Textarea
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
            <FormField
              className="min-w-0 w-full"
              label={label}
              description={description}
              key={key}
            >
              <Input
                required={required}
                placeholder={
                  typeof field["x-placeholder"] === "string"
                    ? field["x-placeholder"]
                    : undefined
                }
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
              />
            </FormField>
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
      <Textarea
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
