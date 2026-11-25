import { Button, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./kubernetes.module.css";

export function MapFields({
  label,
  description,
  value,
  onChange,
  keyLabel,
  valueLabel,
  requireValue = false,
  readOnly = false,
}: {
  label: string;
  description?: string;
  value: Record<string, unknown>;
  keyLabel?: string;
  valueLabel?: string;
  requireValue?: boolean;
  onChange: (next: Record<string, string>) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const serialized = JSON.stringify(value);
  const [source, setSource] = useState(serialized);
  const [rows, setRows] = useState(() =>
    Object.entries(value).map(([name, value], id) => ({
      id,
      name,
      value: typeof value === "string" ? value : "",
    })),
  );
  const [nextId, setNextId] = useState(rows.length);
  if (source !== serialized) {
    setSource(serialized);
    setRows(
      Object.entries(value).map(([name, value], id) => ({
        id,
        name,
        value: typeof value === "string" ? value : "",
      })),
    );
    setNextId(Object.keys(value).length);
  }
  function update(next: typeof rows) {
    setRows(next);
    // Keep incomplete rows local: object conversion would discard duplicates.
    const names = next.map(({ name }) => name);
    if (names.some((name) => !name) || new Set(names).size !== names.length) {
      return;
    }
    const mapping = Object.fromEntries(
      next.map(({ name, value }) => [name, value]),
    );
    setSource(JSON.stringify(mapping));
    onChange(mapping);
  }
  return (
    <fieldset className={styles.fields} disabled={readOnly}>
      <legend className={styles.legend}>{label}</legend>
      {description && <p className={styles.hint}>{description}</p>}
      {rows.map((row, index) => {
        const duplicate = rows.some(
          (other) => other.id !== row.id && other.name === row.name,
        );
        return (
          <div key={row.id} className={styles.pair}>
            <Input
              aria-label={`${label} ${keyLabel ?? t("key")} ${index + 1}`}
              placeholder={keyLabel ?? t("Key")}
              value={row.name}
              required
              ref={(input) =>
                input?.setCustomValidity(
                  duplicate ? t("Keys must be unique.") : "",
                )
              }
              onChange={(event) =>
                update(
                  rows.map((item) =>
                    item.id === row.id
                      ? { ...item, name: event.target.value }
                      : item,
                  ),
                )
              }
            />
            <Input
              aria-label={`${label} ${valueLabel ?? t("value")} ${index + 1}`}
              placeholder={valueLabel ?? t("Value")}
              required={requireValue}
              value={row.value}
              onChange={(event) =>
                update(
                  rows.map((item) =>
                    item.id === row.id
                      ? { ...item, value: event.target.value }
                      : item,
                  ),
                )
              }
            />
            {!readOnly && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                aria-label={`${t("Remove")} ${label} ${index + 1}`}
                onClick={() =>
                  update(rows.filter((item) => item.id !== row.id))
                }
              >
                {t("Remove")}
              </Button>
            )}
            {duplicate && <p role="alert">{t("Keys must be unique.")}</p>}
          </div>
        );
      })}
      {!readOnly && (
        <Button
          type="button"
          variant="outline"
          size="sm"
          className={styles.add}
          onClick={() => {
            update([...rows, { id: nextId, name: "", value: "" }]);
            setNextId(nextId + 1);
          }}
        >
          {t("Add entry")}
        </Button>
      )}
    </fieldset>
  );
}
