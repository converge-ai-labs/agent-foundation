import { Button, DisclosureSection, Tabs, TabsList, TabsTab } from "a13n-ui";
import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import { TextAreaField } from "../../shared/form";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import { jsonObject } from "../../shared/validation";

export function ProviderConfiguration({
  schema,
  text,
  onChange,
  readOnly = false,
  error,
}: {
  schema?: Record<string, unknown>;
  text: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  error?: string;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState("fields");
  const [reset, setReset] = useState(0);
  let value: Record<string, unknown> | undefined;
  try {
    value = jsonObject(text);
  } catch {
    // An unfinished JSON draft remains editable without losing its text.
  }
  const properties =
    schema?.properties && typeof schema.properties === "object"
      ? Object.entries(schema.properties)
      : [];
  const primary = schema?.["x-primary-fields"];
  const common = properties.filter(
    ([key]) => !Array.isArray(primary) || primary.includes(key),
  );
  const advanced = properties.filter(
    ([key]) => Array.isArray(primary) && !primary.includes(key),
  );
  const change = (next: Record<string, unknown>) =>
    onChange(JSON.stringify(next, null, 2));
  const jsonMode = mode === "json" || !schema || !value;
  return (
    <div className={styles.stack}>
      <Tabs value={jsonMode ? "json" : "fields"} onValueChange={setMode}>
        <TabsList aria-label={t("Environment configuration editor")}>
          <TabsTab value="fields" disabled={!schema || !value}>
            {t("Fields")}
          </TabsTab>
          <TabsTab value="json">JSON</TabsTab>
        </TabsList>
      </Tabs>
      {jsonMode || !value ? (
        <TextAreaField
          readOnly={readOnly}
          label={t("Template configuration (JSON)")}
          value={text}
          onChange={onChange}
          error={error}
          code
          rows={8}
        />
      ) : (
        <Fragment key={reset}>
          <fieldset disabled={readOnly}>
            <SchemaFields
              schema={{ ...schema, properties: Object.fromEntries(common) }}
              value={value}
              onChange={change}
            />
          </fieldset>
          {advanced.length > 0 && (
            <DisclosureSection
              title={t("Advanced environment configuration")}
              {...(error ? { open: true } : {})}
            >
              <fieldset disabled={readOnly}>
                <SchemaFields
                  schema={{
                    ...schema,
                    properties: Object.fromEntries(advanced),
                  }}
                  value={value}
                  onChange={change}
                />
              </fieldset>
            </DisclosureSection>
          )}
          {error && <p role="alert">{error}</p>}
        </Fragment>
      )}
      {!readOnly && (
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={() => {
            onChange("{}");
            setReset((value) => value + 1);
          }}
        >
          {t("Reset to defaults")}
        </Button>
      )}
    </div>
  );
}
