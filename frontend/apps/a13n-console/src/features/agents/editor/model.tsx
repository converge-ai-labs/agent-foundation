import {
  Button,
  ChoiceField,
  DisclosureSection,
  FormField,
  Input,
  ReadOnlyField,
  SearchPicker,
} from "a13n-ui";
import { PlusIcon } from "@phosphor-icons/react";
import { useRef, useState, type ReactNode } from "react";
import { useWorkspace } from "../../../layout/workspace";
import { AddModel } from "../../models/add-model";
import {
  ModelIcon,
  ModelKeyLabel,
  providerBrand,
} from "../../models/model-icon";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import { TextAreaField } from "../../../shared/forms";
import { Section } from "../../../shared/page";
import {
  modelOption,
  modelPopupWidth,
} from "../../models/media-understanding-fields";
import type { useAgentChoices } from "../choices";
import { OptionsRefresh } from "../../../shared/forms/options-refresh";
import styles from "./editor.module.css";
import agentStyles from "../agents.module.css";
import type { AgentDraft } from "./draft";

/** The model that powers this agent and how it reasons. */
export function ModelSection({
  id,
  draft,
  choices,
  readOnly,
  mark,
  settings,
  error,
}: {
  id?: string;
  draft: AgentDraft;
  choices: ReturnType<typeof useAgentChoices>;
  readOnly: boolean;
  /** Says how a read-only view's value differs, beside the title. */
  mark?: ReactNode;
  settings?: ReactNode;
  /** Why saving needs a model, after a save without one. */
  error?: string;
}) {
  const { t } = useTranslation();
  const [adding, setAdding] = useState(false);
  const [pickerOpen, setPickerOpen] = useState(false);
  const modelPicker = useRef<HTMLButtonElement>(null);
  const { can, basePath } = useWorkspace();
  return (
    <Section
      id={id}
      title={t("Model")}
      titleActions={
        <OptionsRefresh
          onRefresh={() => choices.refetch({ throwOnError: true })}
        />
      }
      titleLink={{ href: `${basePath}/models`, label: t("Manage models") }}
      description={t(
        "The model that powers the agent, with its reasoning and output limits.",
      )}
      actions={mark}
    >
      {readOnly ? (
        <ReadOnlyField label={t("Model")} hideLabel>
          <span className={styles.readOnlyModel}>
            <ModelKeyLabel modelKey={draft.model} size={20} />
            <span className={styles.readOnlyKey}>{draft.model}</span>
          </span>
        </ReadOnlyField>
      ) : (
        <SearchPicker
          label={t("Model")}
          placeholder={t("Choose a model…")}
          emptyMessage={t(
            "No models available. Configure a provider and model first.",
          )}
          popupClassName={modelPopupWidth}
          value={draft.model}
          groups={[
            {
              label: t("Available models"),
              options:
                choices.data?.models.map((item) => {
                  const provider = choices.data.providers.find(
                    (entry) => entry.id === item.provider_id,
                  );
                  return {
                    ...modelOption(item, provider?.name),
                    icon: (
                      <ModelIcon
                        upstream={item.config.model_name}
                        catalogRef={item.catalog_ref}
                        provider={providerBrand(provider)}
                        size={20}
                      />
                    ),
                  };
                }) ?? [],
            },
          ]}
          onValueChange={draft.setModel}
          open={pickerOpen}
          onOpenChange={setPickerOpen}
          triggerRef={modelPicker}
          footer={
            <>
              {!readOnly && can("write") ? (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  className="w-full justify-start"
                  onClick={() => {
                    setPickerOpen(false);
                    setAdding(true);
                  }}
                >
                  <PlusIcon size={14} aria-hidden="true" />
                  {t("Add model")}
                </Button>
              ) : undefined}
            </>
          }
        />
      )}
      {error && !draft.model && (
        <p role="alert" className={styles.fieldError}>
          {error}
        </p>
      )}
      {settings}
      {adding && (
        <AddModel
          controlledOpen
          requireEnabled
          finalFocus={modelPicker}
          onClose={() => setAdding(false)}
          onSaved={(model) => draft.setModel(model.key)}
        />
      )}
    </Section>
  );
}

/** Optional parameters expand beneath the model, inside its section. */
export function ModelSettings({
  draft,
  thinkingOptions,
  readOnly,
  expanded,
  onExpandedChange,
  validation,
}: {
  draft: AgentDraft;
  thinkingOptions: { value: string; label: string }[];
  readOnly: boolean;
  expanded: boolean;
  onExpandedChange: (value: boolean) => void;
  validation?: Error;
}) {
  const { t, i18n } = useTranslation();
  const summary = [
    draft.thinking !== "default" &&
      thinkingOptions.find((option) => option.value === draft.thinking)?.label,
    draft.maxTokens &&
      t("{{value}} tokens", {
        value: new Intl.NumberFormat(i18n?.language).format(
          Number(draft.maxTokens),
        ),
      }),
    draft.settings.trim() !== "{}" && t("Request overrides"),
  ]
    .filter(Boolean)
    .join(" · ");
  return (
    <DisclosureSection
      className={`${agentStyles.outlinedDisclosure} ${styles.modelSettingsCard}`}
      open={expanded}
      onOpenChange={onExpandedChange}
      title={t("Model settings")}
      summary={summary || t("Defaults")}
    >
      <ChoiceField
        className={styles.modelParameter}
        label={t("Thinking effort")}
        readOnly={readOnly}
        value={draft.thinking}
        onValueChange={draft.setThinking}
        options={thinkingOptions}
      />
      <FormField
        className={styles.modelParameter}
        label={t("Max output tokens")}
        readOnly={readOnly}
      >
        <Input
          type="number"
          min={1}
          step={1}
          placeholder={t("Model default")}
          value={draft.maxTokens}
          onChange={(event) => draft.setMaxTokens(event.target.value)}
        />
      </FormField>
      <TextAreaField
        readOnly={readOnly}
        label={t("Request parameters JSON")}
        hint={t(
          "Request overrides, including extra_body and extra_headers. Store secrets on the provider.",
        )}
        code
        value={draft.settings}
        onChange={draft.setSettings}
        rows={4}
      />
      <ErrorNotice error={validation} />
    </DisclosureSection>
  );
}
