import { useTranslation } from "../i18n";
import { ModelIdentity } from "../shell/model-identity";
import { SettingsRow } from "../shell/settings";
import { Checkbox } from "a13n-ui";
import type { Schema } from "../transport/client";
import styles from "../shell/workbench.module.css";

// An inline checklist shows that several models can be chosen, with each logo
// beside its name. An empty selection is written as absence; IDs without a
// configured model stay visible so they can be cleared.
export function ModelSelectionField({
  label,
  description,
  models,
  value,
  onChange,
}: {
  label: string;
  description: string;
  models: Schema<"ModelSummary">[];
  value: string[];
  onChange: (value: string[] | undefined) => void;
}) {
  const { t } = useTranslation();
  const choices = [
    ...models.map((model) => ({
      value: model.model_id,
      label: model.name,
      model,
    })),
    ...value
      .filter((key) => !models.some((model) => model.model_id === key))
      .map((key) => ({
        value: key,
        label: t("{{id}} (unavailable)", { id: key }),
        model: undefined,
      })),
  ];
  return (
    <div className={styles.modelSelection}>
      <SettingsRow label={label} description={description} className="pb-1">
        {null}
      </SettingsRow>
      <div
        className={`${styles.selectionList} ${styles.modelSelectionList} a13n-scrollbar`}
        role="group"
        aria-label={label}
      >
        {!choices.length && (
          <p>{t("No options yet. Add a configuration first.")}</p>
        )}
        {choices.map((choice) => (
          <label key={choice.value} className={styles.selectionOption}>
            <Checkbox
              checked={value.includes(choice.value)}
              onCheckedChange={(checked) => {
                const next = checked
                  ? [...value, choice.value]
                  : value.filter((key) => key !== choice.value);
                onChange(next.length ? next : undefined);
              }}
            />
            <ModelIdentity
              model={choice.model}
              name={choice.label}
              modelIdentity={choice.model ? undefined : choice.value}
            />
          </label>
        ))}
      </div>
    </div>
  );
}
