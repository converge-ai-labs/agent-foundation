import { ChoiceField, FormField, Input, Textarea } from "a13n-ui";

import { useState, type ReactNode } from "react";

import { useTranslation } from "react-i18next";
import { SwitchField } from "./switch-field";
import styles from "./forms.module.css";
import {
  fieldSchema,
  matchingVariant,
  object,
  objectVariants,
} from "./schema-definition";
import { humanize, translatedFieldLabel } from "./labels";
import { jsonValue } from "./validation";

/** An all-caps enum value (X_SMALL) reads as words too. */
function optionWords(value: string) {
  return humanize(value === value.toUpperCase() ? value.toLowerCase() : value);
}
/** A byte limit also reads as a size, such as 16 MiB. */
function byteSize(value: number) {
  let size = value,
    unit = "B";
  for (const next of ["KiB", "MiB", "GiB"]) {
    if (size < 1024) break;
    size /= 1024;
    unit = next;
  }
  return `${new Intl.NumberFormat(undefined, { maximumFractionDigits: 1 }).format(size)} ${unit}`;
}
function visible(
  field: Record<string, unknown>,
  values: Record<string, unknown>,
) {
  const condition = field["x-visible-when"];
  return (
    !object(condition) ||
    Object.entries(condition).every(
      ([name, expected]) => values[name] === expected,
    )
  );
}

/** Apply schema defaults and fixed values to the submitted object as well as the form. */
export function withSchemaValues(
  schema: Record<string, unknown>,
  value: Record<string, unknown>,
) {
  const variants = objectVariants(schema);
  if (variants.length)
    return withSchemaValues(
      variants[Math.max(0, matchingVariant(variants, value))],
      value,
    );
  const result = { ...value };
  if (object(schema.properties)) {
    for (const [key, definition] of Object.entries(schema.properties)) {
      const field = fieldSchema(definition, schema);
      if (Object.hasOwn(field, "const")) result[key] = field.const;
      else if (result[key] === undefined && Object.hasOwn(field, "default"))
        result[key] = field.default;
      if (
        result[key] !== null &&
        field.type === "object" &&
        object(field.properties)
      ) {
        const nested = withSchemaValues(
          { ...field, $defs: schema.$defs },
          object(result[key]) ? result[key] : {},
        );
        if (Object.keys(nested).length || Object.hasOwn(result, key))
          result[key] = nested;
      }
    }
  }
  if (object(schema.properties))
    for (const [key, definition] of Object.entries(schema.properties)) {
      if (!visible(fieldSchema(definition, schema), result)) delete result[key];
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
  requireFields = true,
  autoFocus = false,
  labelAction,
}: {
  schema: Record<string, unknown>;
  value: Record<string, unknown>;
  onChange: (value: Record<string, unknown>) => void;
  secret?: boolean;
  descriptions?: boolean;
  /** A replacement draft requires its fields; a kept secret does not. */
  requireFields?: boolean;
  /** The first field is where a catalog step lands. */
  autoFocus?: boolean;
  /** Sits beside the first field's label, such as a link to the key page. */
  labelAction?: ReactNode;
}) {
  const { t } = useTranslation();
  const [selected, setSelected] = useState(0);
  const translated = (title: string) => translatedFieldLabel(t, title);
  const variants = objectVariants(schema);
  if (variants.length) {
    const matching = matchingVariant(variants, value);
    const index =
      matching >= 0 ? matching : Math.min(selected, variants.length - 1);
    return (
      <div data-slot="schema-fields" className={styles.schemaFields}>
        <ChoiceField
          label={t("Authentication method")}
          value={String(index)}
          options={variants.map((variant, i) => ({
            value: String(i),
            label: t(String(variant.title ?? `Option ${i + 1}`)),
          }))}
          onValueChange={(next) => {
            setSelected(Number(next));
            onChange(withSchemaValues(variants[Number(next)], {}));
          }}
        />
        <SchemaFields
          key={index}
          schema={variants[index]}
          value={value}
          onChange={onChange}
          secret={secret}
          descriptions={descriptions}
          requireFields={requireFields}
          autoFocus={autoFocus}
          labelAction={labelAction}
        />
      </div>
    );
  }
  const properties = object(schema.properties) ? schema.properties : {};
  function change(key: string, next: unknown) {
    const draft = { ...value };
    if (next === undefined) delete draft[key];
    else draft[key] = next;
    onChange(draft);
  }
  return (
    <div data-slot="schema-fields" className={styles.schemaFields}>
      {Object.entries(properties).map(([key, definition], index) => {
        const field = fieldSchema(definition, schema),
          // A title is prose the console writes in sentence case; a bare key is
          // the schema's own name and stays as it is.
          label =
            typeof field.title === "string"
              ? translated(field.title)
              : translated(humanize(key)),
          first = index === 0,
          required =
            requireFields &&
            Array.isArray(schema.required) &&
            schema.required.includes(key);
        const current = Object.hasOwn(value, key) ? value[key] : field.default;
        const size =
          key.endsWith("_bytes") &&
          typeof current === "number" &&
          current >= 1024
            ? byteSize(current)
            : undefined;
        const description =
          [
            descriptions && typeof field.description === "string"
              ? t(field.description)
              : undefined,
            size,
          ]
            .filter(Boolean)
            .join(" · ") || undefined;
        if (
          Object.hasOwn(field, "const") ||
          !visible(field, withSchemaValues(schema, value))
        )
          return null;
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
                typeof item === "string"
                  ? [{ value: item, label: translated(optionWords(item)) }]
                  : [],
              )
            : [];
        if (options.length)
          return (
            <ChoiceField
              key={key}
              placeholder={t("Select…")}
              value={typeof current === "string" ? current : undefined}
              className={`min-w-0 ${styles.short}`}
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
            <fieldset key={key} className={styles.schemaGroup}>
              {/* A nested object is a group named by its property; one field names itself. */}
              {Object.keys(field.properties).length > 1 && (
                <legend>{label}</legend>
              )}
              <SchemaFields
                schema={{ ...field, $defs: schema.$defs }}
                value={object(current) ? current : {}}
                onChange={(next) => change(key, next)}
                secret={secret}
                descriptions={descriptions}
                requireFields={requireFields}
                autoFocus={first && autoFocus}
                {...(first ? { labelAction } : {})}
              />
            </fieldset>
          );
        }
        if (field.type === "boolean")
          return (
            <SwitchField
              key={key}
              label={label}
              description={description}
              checked={current === true}
              onCheckedChange={(next) => change(key, next)}
            />
          );
        if (
          field.contentMediaType === "application/x-pem-file" ||
          field.format === "multiline"
        )
          return (
            <FormField key={key} label={label} description={description}>
              <Textarea
                autoComplete="off"
                spellCheck={false}
                rows={6}
                value={typeof current === "string" ? current : ""}
                onChange={(event) =>
                  change(key, event.target.value || undefined)
                }
                required={required}
              />
            </FormField>
          );
        if (["string", "number", "integer"].includes(String(field.type)))
          return (
            <FormField
              className={`min-w-0 w-full ${field.type === "string" ? "" : styles.short}`}
              label={label}
              description={description}
              labelAction={first ? labelAction : undefined}
              key={key}
            >
              <Input
                autoFocus={first && autoFocus}
                required={required}
                placeholder={
                  typeof field["x-placeholder"] === "string"
                    ? field["x-placeholder"]
                    : undefined
                }
                type={
                  field.type === "string" &&
                  (secret || field.format === "password")
                    ? "password"
                    : field.type === "string"
                      ? "text"
                      : "number"
                }
                name={key}
                autoComplete={
                  secret || field.format === "password" ? "new-password" : "off"
                }
                value={
                  typeof current === "string" || typeof current === "number"
                    ? current
                    : ""
                }
                onChange={(event) =>
                  change(
                    key,
                    event.target.value === ""
                      ? field.default == null
                        ? undefined
                        : ""
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
            description={description}
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
  description,
  value,
  onChange,
}: {
  label: string;
  description?: string;
  value: unknown;
  onChange: (value: unknown) => void;
}) {
  const [text, setText] = useState(
      value === undefined ? "" : JSON.stringify(value, null, 2),
    ),
    [error, setError] = useState("");
  return (
    <FormField label={label} description={description} error={error}>
      <Textarea
        className={styles.code}
        rows={4}
        value={text}
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
    </FormField>
  );
}
