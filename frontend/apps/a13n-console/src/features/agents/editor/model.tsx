import {
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  SearchPicker,
  SettingsSection,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import { TextAreaField } from "../../../shared/forms";
import { Section } from "../../../shared/page";
import {
  MediaUnderstandingFields,
  modelOption,
  modelPopupWidth,
  useMediaSummary,
  useMediaUnderstandingChoices,
  useWorkspaceMediaDefault,
} from "../../models/media-understanding-fields";
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
  const [mediaExpanded, setMediaExpanded] = useState(false);
  const identity = useMediaUnderstandingChoices();
  const workspaceDefault = useWorkspaceMediaDefault();
  const mediaSummary = useMediaSummary();
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
        popupClassName={modelPopupWidth}
        value={draft.model}
        disabled={readOnly}
        groups={[
          {
            label: t("Available models"),
            options:
              choices.data?.models.map((item) =>
                modelOption(item, identity.providerName(item.provider_id)),
              ) ?? [],
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
          hint={t(
            "Request overrides, including extra_body and extra_headers. Store secrets on the provider.",
          )}
          code
          value={draft.settings}
          onChange={draft.setSettings}
          rows={5}
        />
        <ErrorNotice error={validation} />
      </DisclosureSection>
      <DisclosureSection
        title={t("Media understanding")}
        summary={mediaSummary(
          draft.mediaUnderstanding,
          t("Workspace defaults"),
        )}
        open={mediaExpanded}
        onOpenChange={setMediaExpanded}
      >
        <SettingsSection variant="plain">
          <MediaUnderstandingFields
            value={draft.mediaUnderstanding}
            onChange={draft.setMediaUnderstanding}
            inherit={{
              label: t("Workspace default"),
              describe: (kind) => workspaceDefault(kind) ?? t("Not configured"),
            }}
            disabled={readOnly}
          />
        </SettingsSection>
      </DisclosureSection>
    </Section>
  );
}
