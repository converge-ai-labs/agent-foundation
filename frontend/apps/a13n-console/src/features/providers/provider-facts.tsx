import { SettingsRow } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { fieldLabel } from "../../shared/forms";
import { CopyButton } from "../../shared/identity";
import { schemaProperties } from "./schemas";
import styles from "./providers.module.css";

function humanize(key: string) {
  const words = key.replaceAll(/[_-]+/g, " ").trim();
  return words ? words[0].toLocaleUpperCase() + words.slice(1) : key;
}
const url = (value: string) => /^https?:\/\//i.test(value);
// Endpoints, keys and numbered identifiers read as machine text; a word does
// not, however long it is.
const opaque = (value: string) =>
  url(value) || (!/\s/.test(value) && /[\d_:/@.]/.test(value));

/**
 * The connection facts a saved provider answers for: one row per configured,
 * non-secret value, read through the definition's schema for its name and its
 * defaults. Values the service chose for you stay out of the way.
 */
export function ProviderFacts({
  configuration,
  schema,
  only,
  hideDefaults = false,
}: {
  configuration: Record<string, unknown>;
  schema?: Record<string, unknown> | null;
  /** The values this category shows; omitted, every configured value shows. */
  only?: readonly string[];
  /** Drops values the service chose for you, leaving what was decided here. */
  hideDefaults?: boolean;
}) {
  const { t } = useTranslation();
  const properties = schemaProperties(schema) as Record<
    string,
    { title?: string; default?: unknown }
  >;
  const rows = Object.entries(configuration).filter(([key, value]) => {
    if (only && !only.includes(key)) return false;
    if (value === null || value === undefined || value === "") return false;
    return !hideDefaults || value !== properties[key]?.default;
  });
  return (
    <>
      {rows.map(([key, value]) => {
        const title = properties[key]?.title;
        const text =
          typeof value === "boolean"
            ? t(value ? "Yes" : "No")
            : typeof value === "object"
              ? JSON.stringify(value)
              : String(value);
        const mono = typeof value !== "boolean" && opaque(text);
        return (
          <SettingsRow
            key={key}
            stackOnNarrow={false}
            label={title ? fieldLabel(t(title)) : t(humanize(key))}
          >
            <span className={styles.fact}>
              <span
                className={mono ? styles.factMono : styles.factValue}
                title={text}
              >
                {text}
              </span>
              {typeof value === "string" && url(value) && (
                <CopyButton value={value} iconOnly copyLabel={t("Copy")} />
              )}
            </span>
          </SettingsRow>
        );
      })}
    </>
  );
}
