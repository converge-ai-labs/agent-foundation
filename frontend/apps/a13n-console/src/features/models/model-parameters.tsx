import { Button, DisclosureSection, Tabs, TabsList, TabsTab } from "a13n-ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { TextAreaField } from "../../shared/form";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import { jsonObject } from "../../shared/validation";

const commonLabels: Record<string, string> = {
  max_tokens: "Max output tokens",
  temperature: "Temperature",
  top_p: "Top P",
  openai_reasoning_effort: "Reasoning effort",
};

export function ModelParameters({
  text,
  onChange,
  schema,
  support,
  error,
}: {
  text: string;
  onChange: (value: string) => void;
  schema?: Record<string, unknown>;
  support?: Record<string, string>;
  error?: string;
}) {
  const { t } = useTranslation(),
    [mode, setMode] = useState("fields"),
    [open, setOpen] = useState(false);
  useEffect(() => {
    if (error) {
      setOpen(true);
      setMode("json");
    }
  }, [error]);
  let value: Record<string, unknown> | undefined;
  try {
    value = jsonObject(text);
  } catch {
    /* Keep incomplete JSON editable. */
  }
  const properties = (schema?.properties ?? {}) as Record<
    string,
    Record<string, unknown>
  >;
  const fields = Object.fromEntries(
    Object.entries(properties)
      .filter(([key]) => support?.[`/${key}`] !== "unsupported")
      .map(([key, field]) => [
        key,
        { ...field, title: commonLabels[key] ?? field.title ?? key },
      ]),
  );
  const common = Object.fromEntries(
    Object.entries(fields).filter(([key]) => key in commonLabels),
  );
  const advanced = Object.fromEntries(
    Object.entries(fields).filter(([key]) => !(key in commonLabels)),
  );
  const count = Object.keys(value ?? {}).length;
  const change = (next: Record<string, unknown>) =>
    onChange(JSON.stringify(next, null, 2));
  return (
    <DisclosureSection
      title={t("Parameters")}
      open={open || !!error}
      onOpenChange={setOpen}
      summary={count ? t("{{count}} overrides", { count }) : t("Defaults")}
    >
      <div className={styles.stack}>
        <Tabs value={!schema || !value ? "json" : mode} onValueChange={setMode}>
          <TabsList aria-label={t("Parameter editor")}>
            <TabsTab value="fields" disabled={!schema || !value}>
              {t("Fields")}
            </TabsTab>
            <TabsTab value="json">JSON</TabsTab>
          </TabsList>
        </Tabs>
        {mode === "json" || !schema || !value ? (
          <TextAreaField
            code
            label={t("Settings JSON")}
            error={error}
            value={text}
            onChange={(next) => {
              setMode("json");
              onChange(next);
            }}
            rows={8}
          />
        ) : (
          <>
            <SchemaFields
              schema={{ ...schema, properties: common }}
              value={value}
              onChange={change}
              descriptions={false}
            />
            {Object.keys(advanced).length > 0 && (
              <DisclosureSection title={t("More parameters")}>
                <SchemaFields
                  schema={{ ...schema, properties: advanced }}
                  value={value}
                  onChange={change}
                  descriptions={false}
                />
              </DisclosureSection>
            )}
            {!!count && (
              <div className={styles.stack}>
                <div className={styles.actions}>
                  {Object.keys(value).map((key) => (
                    <Button
                      key={key}
                      type="button"
                      size="sm"
                      variant="outline"
                      aria-label={t("Reset {{name}}", {
                        name: commonLabels[key] ?? key,
                      })}
                      onClick={() => {
                        const next = { ...value };
                        delete next[key];
                        change(next);
                      }}
                    >
                      {t(commonLabels[key] ?? key)} ×
                    </Button>
                  ))}
                </div>
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => onChange("{}")}
                >
                  {t("Reset to defaults")}
                </Button>
              </div>
            )}
          </>
        )}
      </div>
    </DisclosureSection>
  );
}
