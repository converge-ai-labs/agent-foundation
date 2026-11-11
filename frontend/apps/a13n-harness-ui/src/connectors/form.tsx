import { useId } from "react";
import { Button, Input, Switch, Textarea } from "a13n-ui";
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
}: {
  schema: FormSchema;
  root?: FormSchema;
  value: Json;
  onChange: (value: Json) => void;
  disabled?: boolean;
  label?: string;
  required?: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const field = resolveField(schema, root);
  const title = t(label ?? field.title ?? "Value");
  if (field.anyOf) {
    const nonnull = field.anyOf.filter((item) => item.type !== "null");
    if (nonnull.length !== 1)
      return <p role="alert">{t("This form declaration is unsupported.")}</p>;
    return (
      <div className={styles.field}>
        <label className={styles.toggle}>
          <Switch
            checked={value !== null}
            disabled={disabled}
            onCheckedChange={(checked) =>
              onChange(checked ? defaultValue(nonnull[0], root) : null)
            }
            aria-label={title}
          />
          {title}
        </label>
        {value === null ? (
          <small>{t("Using the default selection.")}</small>
        ) : (
          <SchemaForm
            schema={nonnull[0]}
            root={root}
            value={value}
            onChange={onChange}
            disabled={disabled}
            label={title}
            required={required}
          />
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
          />
        ))}
      </div>
    );
  }
  if (field.type === "array") {
    const items = Array.isArray(value) ? value : [];
    const itemSchema = field.items ?? { type: "string" };
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
              label={t("Item {{number}}", { number: index + 1 })}
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
          {t("Add item")}
        </Button>
      </fieldset>
    );
  }
  const numeric = field.type === "integer" || field.type === "number";
  return (
    <div className={styles.field}>
      {field.type === "boolean" ? (
        <label className={styles.toggle} htmlFor={id}>
          <Switch
            id={id}
            checked={!!value}
            disabled={disabled}
            onCheckedChange={onChange}
          />
          {title}
        </label>
      ) : (
        <>
          <label htmlFor={id}>
            {title}
            {required ? " *" : ""}
          </label>
          {field.enum ? (
            <select
              id={id}
              className={styles.select}
              disabled={disabled}
              value={JSON.stringify(value)}
              onChange={(event) =>
                onChange(JSON.parse(event.target.value) as Json)
              }
            >
              {field.enum.map((item) => (
                <option key={JSON.stringify(item)} value={JSON.stringify(item)}>
                  {String(item)}
                </option>
              ))}
            </select>
          ) : field.format === "textarea" && !field.writeOnly ? (
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
              aria-describedby={field.description ? `${id}-help` : undefined}
            />
          )}
        </>
      )}
      {field.description && (
        <small id={`${id}-help`}>{t(field.description)}</small>
      )}
    </div>
  );
}
