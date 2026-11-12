import { DisclosureSection, FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { fieldSchema, object } from "../../shared/forms/schema-definition";
import { humanize } from "../../shared/forms/labels";
import { ConfigurationGroup } from "./configuration-group";
import styles from "./environments.module.css";

const commonFields = [
  "max_sessions",
  "max_concurrent_file_transfers",
  "max_file_bytes",
  "max_output_preview_bytes",
  "max_output_bytes_per_stream",
];

const groups = [
  ["capacity", "Capacity"],
  ["sizes", "File and output sizes"],
  ["retention", "Retention and transfer cleanup"],
  ["deadlines", "Operation and startup deadlines"],
  ["lifecycle", "Session lifetime defaults"],
  ["protocol", "Protocol sizing"],
] as const;

/** Shared daemon configuration; provider resources and Session policy live elsewhere. */
export function EnvdSettings({
  schema,
  recipe,
  onChange,
  readOnly = false,
}: {
  schema: Record<string, unknown>;
  recipe: Record<string, unknown>;
  onChange: (recipe: Record<string, unknown>) => void;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const [openGroups, setOpenGroups] = useState<string[]>([]);
  const rootProperties = object(schema.properties) ? schema.properties : {};
  if (!rootProperties.envd) return null;
  const settingsSchema = fieldSchema(rootProperties.envd, schema);
  const properties = object(settingsSchema.properties)
    ? settingsSchema.properties
    : {};
  const limitsSchema = fieldSchema(properties.limits, schema);
  const limitProperties = object(limitsSchema.properties)
    ? limitsSchema.properties
    : {};
  const settings = object(recipe.envd) ? recipe.envd : {};
  const limits = object(settings.limits) ? settings.limits : {};
  // Docker's established template fields remain fallbacks for omitted Envd overrides.
  const legacy: Record<string, unknown> = {};
  if (rootProperties.max_output_bytes_per_stream) {
    for (const key of [
      "max_file_bytes",
      "max_output_preview_bytes",
      "max_output_bytes_per_stream",
      "max_spool_bytes",
    ]) {
      legacy[key] =
        recipe[key] ?? fieldSchema(rootProperties[key], schema).default;
    }
    legacy.max_processes =
      recipe.max_concurrent_processes ??
      fieldSchema(rootProperties.max_concurrent_processes, schema).default;
  }
  function update(key: string, raw: string, nested: boolean, scale: number) {
    const target = { ...(nested ? limits : settings) };
    if (raw === "") delete target[key];
    else target[key] = Math.round(Number(raw) * scale);
    const next = nested ? { ...settings, limits: target } : target;
    if (object(next.limits) && !Object.keys(next.limits).length)
      delete next.limits;
    const draft = { ...recipe };
    if (Object.keys(next).length) draft.envd = next;
    else delete draft.envd;
    onChange(draft);
  }
  function fields(group: string) {
    return [
      ...Object.entries(limitProperties).map(([key, definition]) => ({
        key,
        definition,
        nested: true,
      })),
      ...Object.entries(properties)
        .filter(([key]) => key !== "limits")
        .map(([key, definition]) => ({
          key,
          definition,
          nested: false,
        })),
    ]
      .sort(
        (left, right) =>
          Number(commonFields.includes(right.key)) -
          Number(commonFields.includes(left.key)),
      )
      .map(({ key, definition, nested }) => {
        const field = fieldSchema(definition, schema);
        if (field["x-envd-group"] !== group) return null;
        const unit = field["x-envd-unit"];
        const scale = unit === "milliseconds" ? 1000 : 1;
        const selected = (nested ? limits : settings)[key];
        const fallback = (nested ? legacy[key] : undefined) ?? field.default;
        const label = t(humanize(key.replace(/_ms$/, "")));
        return (
          <FormField
            data-field-layout="stacked"
            key={key}
            label={label}
            description={unit === "milliseconds" ? t("Seconds") : undefined}
          >
            <Input
              type="number"
              name={`envd.${nested ? "limits." : ""}${key}`}
              aria-label={label}
              value={typeof selected === "number" ? selected / scale : ""}
              placeholder={
                typeof fallback === "number"
                  ? String(fallback / scale)
                  : t("No limit")
              }
              min={Number(field.minimum ?? 1) / scale}
              max={
                Math.min(
                  Number(field.maximum ?? Number.MAX_SAFE_INTEGER),
                  Number.MAX_SAFE_INTEGER,
                ) / scale
              }
              step={1 / scale}
              onChange={(event) =>
                update(key, event.target.value, nested, scale)
              }
            />
          </FormField>
        );
      });
  }
  return (
    <ConfigurationGroup title={t("Envd settings")}>
      <div className={styles.envdSettings}>
        {groups.map(([group, title]) => (
          <DisclosureSection
            key={group}
            title={t(title)}
            keepMounted
            open={openGroups.includes(group)}
            onOpenChange={(open) =>
              setOpenGroups((current) =>
                open
                  ? [...current, group]
                  : current.filter((item) => item !== group),
              )
            }
            onInvalidCapture={() =>
              setOpenGroups((current) =>
                current.includes(group) ? current : [...current, group],
              )
            }
          >
            <fieldset disabled={readOnly} className={styles.envdFields}>
              {fields(group)}
            </fieldset>
          </DisclosureSection>
        ))}
      </div>
    </ConfigurationGroup>
  );
}
