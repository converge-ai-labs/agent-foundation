import {
  Button,
  DisclosureSection,
  ToggleGroup,
  ToggleGroupItem,
} from "a13n-ui";
import { Fragment, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { StatePill } from "../../shared/feedback";
import { jsonObject, SchemaFields, TextAreaField } from "../../shared/forms";
import styles from "./environments.module.css";

/**
 * The provider-specific part of a template revision. Ordinary fields lead;
 * the JSON escape hatch is one toggle away; mounts and rare settings stay in
 * disclosures.
 */
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
  const fieldsAvailable = !!schema && !!value;
  const jsonMode = mode === "json" || !fieldsAvailable;
  return (
    <div className="grid min-w-0 gap-4">
      <div className={styles.groupHeader}>
        <div className="min-w-0">
          <h4>{t("Configuration")}</h4>
        </div>
        <ToggleGroup
          className={styles.segmented}
          variant="outline"
          size="sm"
          value={[jsonMode ? "json" : "fields"]}
          onValueChange={(next) => {
            if (next[0]) setMode(next[0]);
          }}
          aria-label={t("Configuration editor")}
        >
          <ToggleGroupItem value="fields" disabled={!fieldsAvailable}>
            {t("Fields")}
          </ToggleGroupItem>
          <ToggleGroupItem value="json">JSON</ToggleGroupItem>
        </ToggleGroup>
      </div>
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
            <div className="grid min-w-0 gap-3">
              <fieldset disabled={readOnly}>
                <SchemaFields
                  schema={{ ...schema, properties: Object.fromEntries(image) }}
                  value={value}
                  onChange={change}
                />
              </fieldset>
              <div className={styles.testRow}>
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  loading={imageTest?.pending}
                  onClick={imageTest?.run}
                >
                  {t("Test image")}
                </Button>
                <ImageTestResult
                  pending={imageTest?.pending}
                  result={imageTest?.result}
                  error={imageTest?.error}
                />
              </div>
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
          {error && (
            <p role="alert" className="text-destructive-foreground text-xs">
              {error}
            </p>
          )}
        </Fragment>
      )}
      {!readOnly && (
        <div>
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
        </div>
      )}
    </div>
  );
}

/** The image test reports as a pill plus the detail the Worker returned. */
function ImageTestResult({
  pending,
  result,
  error,
}: {
  pending?: boolean;
  result?: Schema["ImageTestResponse"];
  error?: Error | null;
}) {
  const { t } = useTranslation();
  if (pending)
    return (
      <p className={styles.testResult} role="status">
        {t("Testing image…")}
      </p>
    );
  if (error)
    return (
      <>
        <StatePill state="failed" />
        <p className={styles.testResult} role="alert">
          {error.message}
        </p>
      </>
    );
  if (!result) return null;
  if (result.error)
    return (
      <>
        <StatePill state="failed" />
        <p className={styles.testResult} role="status">
          {result.error}
        </p>
      </>
    );
  return (
    <>
      <StatePill state="succeeded" />
      <p className={styles.testResult} role="status">
        {t("Image {{id}} passed: {{checks}}", {
          id: result.image_id,
          checks: result.checks?.join(", "),
        })}
      </p>
    </>
  );
}
