import { Button, Input } from "a13n-ui";
import { useId, useState } from "react";
import { useTranslation } from "react-i18next";
import styles from "./environment-variables.module.css";

type Row = { id: number; name: string; value: string };

/** Keep incomplete names and duplicate drafts visible until the user fixes them. */
export function EnvironmentVariables({
  value,
  onChange,
  readOnly = false,
}: {
  value: Record<string, string>;
  onChange: (value: Record<string, string>) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const id = useId();
  const serialized = JSON.stringify(value);
  const [source, setSource] = useState(serialized);
  const [rows, setRows] = useState<Row[]>(() =>
    Object.entries(value).map(([name, value], id) => ({ id, name, value })),
  );
  const [nextId, setNextId] = useState(rows.length);
  if (source !== serialized) {
    setSource(serialized);
    setRows(
      Object.entries(value).map(([name, value], id) => ({ id, name, value })),
    );
    setNextId(Object.keys(value).length);
  }
  function update(next: Row[]) {
    setRows(next);
    const mapping = Object.fromEntries(
      next.map((row) => [row.name, row.value]),
    );
    setSource(JSON.stringify(mapping));
    onChange(mapping);
  }
  return (
    <fieldset
      disabled={readOnly}
      className={styles.environmentVariables}
      aria-describedby={`${id}-help`}
    >
      <legend>{t("Environment variables")}</legend>
      <p id={`${id}-help`}>
        {t(
          "Defaults for commands and child processes. Values are stored as ordinary configuration, not secrets.",
        )}
      </p>
      {rows.map((row, index) => {
        const duplicate = rows.some(
          (other) => other.id !== row.id && other.name === row.name,
        );
        return (
          <div className={styles.environmentVariableRow} key={row.id}>
            <Input
              aria-label={t("Variable name {{number}}", { number: index + 1 })}
              placeholder="APP_MODE"
              value={row.name}
              required
              pattern="[A-Za-z_][A-Za-z0-9_]*"
              ref={(input) => {
                input?.setCustomValidity(
                  duplicate ? t("Variable names must be unique.") : "",
                );
              }}
              aria-invalid={duplicate || undefined}
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
              aria-label={t("Variable value {{number}}", { number: index + 1 })}
              placeholder={t("Value")}
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
            <Button
              type="button"
              variant="ghost"
              aria-label={t("Remove variable {{number}}", {
                number: index + 1,
              })}
              onClick={() => update(rows.filter((item) => item.id !== row.id))}
            >
              {t("Remove")}
            </Button>
            {duplicate && (
              <p role="alert">{t("Variable names must be unique.")}</p>
            )}
          </div>
        );
      })}
      <Button
        type="button"
        variant="outline"
        className={styles.addEnvironmentVariable}
        onClick={() => {
          update([...rows, { id: nextId, name: "", value: "" }]);
          setNextId(nextId + 1);
        }}
      >
        {t("Add variable")}
      </Button>
    </fieldset>
  );
}
