import {
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  SearchPicker,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import { TextAreaField } from "../../../shared/forms";
import { Section } from "../../../shared/page";
import { ModelIcon } from "../../models/model-icon";
import type { useAgentChoices } from "../choices";
import styles from "./editor.module.css";
import type { AgentDraft } from "./draft";

/** The model that powers this agent and how it reasons. */
export function ModelSection({
  draft,
  choices,
  thinkingOptions,
  readOnly,
  expanded,
  onExpandedChange,
  validation,
}: {
  draft: AgentDraft;
  choices: ReturnType<typeof useAgentChoices>;
  thinkingOptions: { value: string; label: string }[];
  readOnly: boolean;
  expanded: boolean;
  onExpandedChange: (value: boolean) => void;
  validation?: Error;
}) {
  const { t } = useTranslation();
  return (
    <Section
      title={t("Model")}
      description={t("The model that powers this agent and how it reasons.")}
    >
      <SearchPicker
        label={t("Model")}
        placeholder={t("Choose a model…")}
        emptyMessage={t(
          "No models available. Configure a provider and model first.",
        )}
        value={draft.model}
        disabled={readOnly}
        groups={[
          {
            label: t("Available models"),
            options:
              choices.data?.models.map((item) => ({
                value: item.key,
                label: item.name,
                icon: (
                  <ModelIcon
                    upstream={item.upstream_model}
                    catalogRef={item.catalog_ref}
                    size={20}
                  />
                ),
                description: [...new Set([item.key, item.upstream_model])]
                  .filter((value) => value !== item.name)
                  .join(" · "),
              })) ?? [],
          },
        ]}
        onValueChange={draft.setModel}
      />
      <div className={styles.grid}>
        <ChoiceField
          label={t("Thinking effort")}
          readOnly={readOnly}
          value={draft.thinking}
          onValueChange={draft.setThinking}
          options={thinkingOptions}
        />
        <FormField label={t("Max output tokens")} readOnly={readOnly}>
          <Input
            type="number"
            min={1}
            step={1}
            placeholder={t("Model default")}
            value={draft.maxTokens}
            onChange={(event) => draft.setMaxTokens(event.target.value)}
          />
        </FormField>
      </div>
      <DisclosureSection
        title={t("Provider-specific settings")}
        summary={
          Object.keys(draft.initialExtraSettings).length ||
          draft.settings.trim() !== "{}"
            ? t("Customized")
            : t("Defaults")
        }
        open={expanded}
        onOpenChange={onExpandedChange}
      >
        <TextAreaField
          readOnly={readOnly}
          label={t("Provider-specific settings")}
          hideLabel
          hint={t("JSON passed to the provider on top of the model defaults.")}
          code
          value={draft.settings}
          onChange={draft.setSettings}
          rows={5}
        />
        <ErrorNotice error={validation} />
      </DisclosureSection>
    </Section>
  );
}
