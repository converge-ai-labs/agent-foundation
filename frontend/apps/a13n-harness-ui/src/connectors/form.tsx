import { useId } from "react";
import {
  Button,
  ChoiceField,
  FormField,
  Input,
  StatusPill,
  Switch,
  Textarea,
} from "a13n-ui";
import { useTranslation } from "../i18n";
import {
  defaultValue,
  resolveField,
  type FormSchema,
  type Json,
} from "./schema";
import styles from "./connectors.module.css";

export function SchemaForm({
  schema,
  value,
  onChange,
  disabled = false,
  root = schema,
  label,
  required = false,
  path = "",
}: {
  schema: FormSchema;
  root?: FormSchema;
  value: Json;
  onChange: (value: Json) => void;
  disabled?: boolean;
  label?: string;
  required?: boolean;
  path?: string;
}) {
  const { t } = useTranslation();
  const id = useId();
  const field = resolveField(schema, root);
  const title = t(label ?? field.title ?? "Value");
  const saved = field.writeOnly && root["x-a13n-saved"]?.includes(path);
  if (field.anyOf) {
    const nonnull = field.anyOf.filter((item) => item.type !== "null");
    if (nonnull.length !== 1)
      return <p role="alert">{t("This form declaration is unsupported.")}</p>;
    const array = nonnull[0].type === "array";
    return (
      <div className={styles.fields}>
        <ChoiceField
          label={title}
          value={value === null ? "default" : "selected"}
          disabled={disabled}
          options={[
            {
              value: "default",
              label: t(field["x-a13n-null-label"] ?? "Default selection"),
            },
            {
              value: "selected",
              label: t(field["x-a13n-selected-label"] ?? "Only selected items"),
            },
          ]}
          onValueChange={(next) =>
            onChange(next === "default" ? null : defaultValue(nonnull[0], root))
          }
        />
        {value !== null && (
          <>
            {array && Array.isArray(value) && value.length === 0 && (
              <p role="status" className={styles.warning}>
                {t(field["x-a13n-empty-label"] ?? "No items are selected")}
              </p>
            )}
            <SchemaForm
              schema={{
                ...nonnull[0],
                "x-a13n-add-label": field["x-a13n-add-label"],
              }}
              root={root}
              value={value}
              onChange={onChange}
              disabled={disabled}
              label={title}
              required={required}
              path={path}
            />
          </>
        )}
      </div>
    );
  }
  if (field.type === "object" || field.properties) {
    const object =
      value && !Array.isArray(value) && typeof value === "object" ? value : {};
    return (
      <div className={styles.fields}>
        {Object.entries(field.properties ?? {}).map(([key, child]) => (
          <SchemaForm
            key={key}
            schema={child}
            root={root}
            value={object[key] ?? defaultValue(child, root)}
            onChange={(next) => onChange({ ...object, [key]: next })}
            disabled={disabled}
            label={child.title ?? key}
            required={field.required?.includes(key)}
            path={`${path}/${key.replaceAll("~", "~0").replaceAll("/", "~1")}`}
          />
        ))}
      </div>
    );
  }
  if (field.type === "array") {
    const items = Array.isArray(value) ? value : [];
    const itemSchema = field.items ?? { type: "string" };
    const resolved = resolveField(itemSchema, root);
    return (
      <fieldset className={styles.array} disabled={disabled}>
        <legend>{title}</legend>
        {field.description && <small>{t(field.description)}</small>}
        {items.map((item, index) => (
          <div key={index} className={styles.arrayRow}>
            <SchemaForm
              schema={itemSchema}
              root={root}
              value={item}
              onChange={(next) =>
                onChange(
                  items.map((prior, position) =>
                    position === index ? next : prior,
                  ),
                )
              }
              disabled={disabled}
              label={
                resolved.type === "object"
                  ? undefined
                  : t("Item {{number}}", { number: index + 1 })
              }
              path={`${path}/${index}`}
            />
            <Button
              variant="ghost"
              size="sm"
              type="button"
              aria-label={t("Remove item {{number}}", { number: index + 1 })}
              onClick={() =>
                onChange(items.filter((_, position) => position !== index))
              }
            >
              {t("Remove")}
            </Button>
          </div>
        ))}
        <Button
          variant="outline"
          size="sm"
          type="button"
          onClick={() => onChange([...items, defaultValue(itemSchema, root)])}
        >
          {t(field["x-a13n-add-label"] ?? "Add item")}
        </Button>
      </fieldset>
    );
  }
  if (field.type === "boolean")
    return (
      <div className={styles.field}>
        <label className={styles.toggle} htmlFor={id}>
          <Switch
            id={id}
            checked={!!value}
            disabled={disabled}
            onCheckedChange={onChange}
          />
          {title}
        </label>
        {field.description && <small>{t(field.description)}</small>}
      </div>
    );
  if (field.enum)
    return (
      <ChoiceField
        label={title}
        description={field.description && t(field.description)}
        value={JSON.stringify(value)}
        onValueChange={(next) => onChange(JSON.parse(next) as Json)}
        disabled={disabled}
        required={required}
        options={field.enum.map((item) => ({
          value: JSON.stringify(item),
          label: t(String(item)),
        }))}
      />
    );
  const numeric = field.type === "integer" || field.type === "number";
  return (
    <FormField
      label={
        <>
          {title}
          {required ? " *" : ""}
        </>
      }
      labelAction={saved ? <StatusPill>{t("Saved")}</StatusPill> : undefined}
      description={field.description && t(field.description)}
    >
      {field.format === "textarea" && !field.writeOnly ? (
        <Textarea
          id={id}
          value={String(value ?? "")}
          onChange={(event) => onChange(event.target.value)}
          disabled={disabled}
          required={required}
        />
      ) : (
        <Input
          id={id}
          type={field.writeOnly ? "password" : numeric ? "number" : "text"}
          value={String(value ?? "")}
          placeholder={saved ? t("Enter a value to replace it") : undefined}
          onChange={(event) =>
            onChange(
              numeric && event.target.value !== ""
                ? Number(event.target.value)
                : event.target.value,
            )
          }
          disabled={disabled}
          required={required}
          autoComplete={field.writeOnly ? "new-password" : "off"}
          spellCheck={false}
          min={field.minimum}
          max={field.maximum}
          minLength={field.minLength}
          maxLength={field.maxLength}
          step={field.type === "integer" ? 1 : "any"}
        />
      )}
    </FormField>
  );
}
