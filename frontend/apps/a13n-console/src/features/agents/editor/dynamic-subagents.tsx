import { XIcon } from "@phosphor-icons/react";
import {
  Button,
  ChoiceField,
  FormField,
  ReadOnlyField,
  SearchPicker,
} from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../../shared/feedback";
import { SwitchField } from "../../../shared/forms";
import { useAgentChoices } from "../choices";
import {
  InheritIcon,
  modelOption,
  modelPopupWidth,
} from "../../models/media-understanding-fields";
import { ModelKeyLabel } from "../../models/model-icon";
import { useModelProviderDefinitions } from "../../models/provider-definitions";
import { thinkingEfforts, thinkingSelection, type AgentDraft } from "./draft";
import styles from "./subagents.module.css";

export function DynamicSubagentsSettings({
  draft,
  readOnly,
}: {
  draft: AgentDraft;
  readOnly: boolean;
}) {
  const { t } = useTranslation();
  const choices = useAgentChoices();
  const definitions = useModelProviderDefinitions();
  const policy = draft.dynamicSubagents;
  const update = (changes: Partial<typeof policy>) =>
    draft.setDynamicSubagents({ ...policy, ...changes });
  const allowed = policy.allowed_models ?? [];
  const models = choices.data?.models ?? [];
  const provider = (id: string) =>
    choices.data?.providers.find((item) => item.id === id);
  const eligible = models.filter((item) => provider(item.provider_id)?.enabled);
  const selected = models.find(
    (item) => item.key === (policy.default_model ?? draft.model),
  );
  const schema = definitions.data?.items
    .map((item) => item.settings_schemas?.[selected?.config.model_api ?? ""])
    .find(Boolean);
  const thinking = thinkingSelection(policy.default_thinking);
  const thinkingOptions = [
    { value: "default", label: t("Inherit") },
    { value: "true", label: t("On (default effort)") },
    { value: "false", label: t("Off") },
    ...thinkingEfforts(schema).map((value) => ({
      value,
      label: t(value.charAt(0).toUpperCase() + value.slice(1)),
    })),
  ];
  if (!thinkingOptions.some((option) => option.value === thinking))
    thinkingOptions.push({ value: thinking, label: thinking });
  const permitted = [...new Set([draft.model, ...allowed])].filter(Boolean);
  const defaultOptions = permitted.map((key) => {
    const model = eligible.find((item) => item.key === key);
    return model
      ? modelOption(model, provider(model.provider_id))
      : {
          value: key,
          label: key,
          description: t("Unavailable"),
          disabled: true,
        };
  });
  if (policy.default_model && !permitted.includes(policy.default_model))
    defaultOptions.push({
      value: policy.default_model,
      label: policy.default_model,
      description: t("Unavailable"),
      disabled: true,
    });
  const invalidDefault =
    policy.default_model && !permitted.includes(policy.default_model);
  return (
    <div className={styles.dynamic}>
      <SwitchField
        label={t("Dynamic subagents")}
        description={t(
          "Let this agent create task-specific agents using its own capabilities.",
        )}
        readOnly={readOnly}
        checked={policy.enabled ?? false}
        onCheckedChange={(enabled) => update({ enabled })}
      />
      {policy.enabled && (
        <>
          <div className={styles.settings}>
            {readOnly ? (
              <ReadOnlyField
                label={t("Default model")}
                data-field-layout="setting"
              >
                {policy.default_model ? (
                  <ModelKeyLabel
                    modelKey={policy.default_model}
                    className={styles.modelLabel}
                  />
                ) : (
                  t("Parent model")
                )}
              </ReadOnlyField>
            ) : (
              <FormField
                label={t("Default model")}
                description={t(
                  "Used unless the agent selects another allowed model.",
                )}
                error={
                  invalidDefault
                    ? t(
                        "The default model must be the parent model or an allowed model.",
                      )
                    : undefined
                }
              >
                <SearchPicker
                  label={t("Default subagent model")}
                  placeholder={t("Parent model")}
                  emptyMessage={t("No models available.")}
                  popupClassName={modelPopupWidth}
                  value={policy.default_model ?? ""}
                  onValueChange={(value) =>
                    update({ default_model: value || null })
                  }
                  groups={[
                    {
                      label: t("Available models"),
                      options: [
                        {
                          value: "",
                          label: t("Parent model"),
                          description:
                            models.find((item) => item.key === draft.model)
                              ?.name ?? draft.model,
                          icon: <InheritIcon />,
                        },
                        ...defaultOptions,
                      ],
                    },
                  ]}
                />
              </FormField>
            )}
            <ChoiceField
              label={t("Thinking effort")}
              description={t(
                "Default effort when the agent does not choose one.",
              )}
              readOnly={readOnly}
              value={thinking}
              options={thinkingOptions}
              onValueChange={(value) =>
                update({
                  default_thinking:
                    value === "default"
                      ? null
                      : value === "true"
                        ? true
                        : value === "false"
                          ? false
                          : value,
                })
              }
            />
            <div>
              {readOnly ? (
                <ReadOnlyField
                  label={t("Additional models")}
                  description={t("The parent model is always allowed.")}
                  data-field-layout="setting"
                >
                  {allowed.length ? allowed.length : t("None")}
                </ReadOnlyField>
              ) : (
                <FormField
                  label={t("Additional models")}
                  description={t("The parent model is always allowed.")}
                >
                  <SearchPicker
                    label={t("Add allowed model")}
                    placeholder={t("Add model")}
                    emptyMessage={t("No additional models available.")}
                    popupClassName={modelPopupWidth}
                    value=""
                    groups={[
                      {
                        label: t("Available models"),
                        options: eligible
                          .filter(
                            (item) =>
                              item.key !== draft.model &&
                              !allowed.includes(item.key),
                          )
                          .map((item) =>
                            modelOption(item, provider(item.provider_id)),
                          ),
                      },
                    ]}
                    onValueChange={(value) =>
                      update({ allowed_models: [...allowed, value] })
                    }
                  />
                </FormField>
              )}
              {!!allowed.length && (
                <ul
                  className={styles.models}
                  aria-label={t("Additional allowed models")}
                >
                  {allowed.map((key) => (
                    <li key={key}>
                      <ModelKeyLabel modelKey={key} />
                      {!readOnly && (
                        <Button
                          type="button"
                          size="icon-xs"
                          variant="ghost"
                          aria-label={t("Remove allowed model {{model}}", {
                            model: key,
                          })}
                          onClick={() =>
                            update({
                              allowed_models: allowed.filter(
                                (item) => item !== key,
                              ),
                              ...(policy.default_model === key
                                ? { default_model: null }
                                : {}),
                            })
                          }
                        >
                          <XIcon />
                        </Button>
                      )}
                    </li>
                  ))}
                </ul>
              )}
            </div>
          </div>
          {draft.subagentMode !== "async" && (
            <p role="alert" className={styles.error}>
              {t("Dynamic subagents require Async mode.")}
            </p>
          )}
          <ErrorNotice error={choices.error ?? definitions.error} />
        </>
      )}
    </div>
  );
}
