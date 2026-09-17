import { Button, DisclosureSection, Tabs, TabsList, TabsTab } from "a13n-ui";
import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import { TextAreaField } from "../../shared/form";
import type { Schema } from "../../shared/api";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import { jsonObject } from "../../shared/validation";

export function ProviderConfiguration({
  schema,
  text,
  onChange,
  readOnly = false,
  error,
  imageTest,
  variant = "default",
}: {
  schema?: Record<string, unknown>;
  text: string;
  onChange: (value: string) => void;
  readOnly?: boolean;
  error?: string;
  variant?: "default" | "docker";
  imageTest?: {
    run: () => void;
    pending: boolean;
    result?: Schema["ImageTestResponse"];
    error?: Error | null;
  };
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
  const image: typeof properties = [];
  const mounts: typeof properties = [];
  const common: typeof properties = [];
  const advanced: typeof properties = [];
  for (const property of properties) {
    const key = property[0];
    if (variant === "docker" && key === "image" && imageTest)
      image.push(property);
    else if (variant === "docker" && key === "mounts") mounts.push(property);
    else if (!Array.isArray(primary) || primary.includes(key))
      common.push(property);
    else advanced.push(property);
  }
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
          {image.length > 0 && (
            <div className={styles.stack}>
              <fieldset disabled={readOnly}>
                <SchemaFields
                  schema={{ ...schema, properties: Object.fromEntries(image) }}
                  value={value}
                  onChange={change}
                />
              </fieldset>
              <Button
                type="button"
                size="sm"
                disabled={imageTest?.pending}
                onClick={imageTest?.run}
              >
                {imageTest?.pending ? t("Testing image…") : t("Test image")}
              </Button>
              {imageTest?.result && (
                <p role="status">
                  {imageTest.result.error
                    ? imageTest.result.error
                    : t("Image {{id}} passed: {{checks}}", {
                        id: imageTest.result.image_id,
                        checks: imageTest.result.checks?.join(", "),
                      })}
                </p>
              )}
              {imageTest?.error && (
                <p role="alert">{imageTest.error.message}</p>
              )}
            </div>
          )}
          <fieldset disabled={readOnly}>
            <SchemaFields
              schema={{ ...schema, properties: Object.fromEntries(common) }}
              value={value}
              onChange={change}
            />
          </fieldset>
          {mounts.length > 0 && (
            <DisclosureSection title={t("Mounts")}>
              <fieldset disabled={readOnly}>
                <SchemaFields
                  schema={{ ...schema, properties: Object.fromEntries(mounts) }}
                  value={value}
                  onChange={change}
                />
              </fieldset>
            </DisclosureSection>
          )}
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
