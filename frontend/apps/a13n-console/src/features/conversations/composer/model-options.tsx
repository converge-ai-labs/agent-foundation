import {
  ChoiceField,
  DisclosureSection,
  FormField,
  SearchPicker,
  SettingsSection,
} from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, type Schema } from "../../../shared/api";
import { isRecord } from "../../../service-client";
import { jsonObject } from "../../../shared/forms";
import { ErrorNotice } from "../../../shared/feedback";
import { enumValues } from "../../models/model-settings";
import { useModelProviderDefinitions } from "../../models/provider-definitions";
import {
  InheritIcon,
  MediaUnderstandingFields,
  modelOption,
  modelPopupWidth,
  useMediaSummary,
  useMediaUnderstandingChoices,
  useWorkspaceMediaDefault,
} from "../../models/media-understanding-fields";
import type { RunOptionsState } from "./options-dialog";
/**
 * Calling-API schemas supply thinking choices. The picker edits the same
 * model_settings object as advanced JSON, preserving unrepresented parameters.
 */
export function ModelOptions({
  options,
  models,
  agentId,
  revisionId,
}: {
  options: RunOptionsState;
  models?: Schema["Model"][];
  agentId?: string;
  revisionId?: string;
}) {
  const { t } = useTranslation();
  const identity = useMediaUnderstandingChoices();
  const client = useClient();
  const { workspace } = useWorkspace();
  const definitions = useModelProviderDefinitions();
  const revision = useQuery({
    queryKey: ["run-option-revision", workspace.id, agentId, revisionId],
    enabled: !!agentId && !!revisionId && !options.model,
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/agents/{agent_id}/revisions/{revision_id}", {
          params: { path: { agent_id: agentId!, revision_id: revisionId! } },
          signal,
        })
        .then(data),
  });
  const selectedModel = models?.find(
    (model) => model.key === (options.model || revision.data?.config.model),
  );
  const api = selectedModel?.config.model_api;
  const schema = api
    ? definitions.data?.items
        .map((item) => item.settings_schemas?.[api])
        .find(Boolean)
    : undefined;
  const properties = isRecord(schema?.properties) ? schema.properties : {};
  let extra: Record<string, Schema["JsonValue"]> = {};
  let invalid = false;
  try {
    extra = jsonObject(options.advanced);
  } catch {
    invalid = true;
  }
  const settings = isRecord(extra.model_settings) ? extra.model_settings : {};
  const thinking = settings.thinking;
  const thinkingValue =
    thinking === undefined ? "inherit" : JSON.stringify(thinking);
  const thinkingOptions = [
    { value: "inherit", label: t("Inherit") },
    { value: "true", label: t("On (default effort)") },
    { value: "false", label: t("Off") },
    ...enumValues(properties.thinking).map((effort) => ({
      value: JSON.stringify(effort),
      label: t(effort.charAt(0).toUpperCase() + effort.slice(1)),
    })),
  ];
  if (!thinkingOptions.some((item) => item.value === thinkingValue))
    thinkingOptions.push({ value: thinkingValue, label: String(thinking) });
  return (
    <>
      <FormField label={t("Model")}>
        <SearchPicker
          id="run-option-model"
          label={t("Model")}
          placeholder={t("Search models…")}
          emptyMessage={t(
            "No models available. Configure a provider and model first.",
          )}
          popupClassName={modelPopupWidth}
          value={options.model || "inherit"}
          groups={[
            {
              label: t("Model"),
              options: [
                {
                  value: "inherit",
                  label: t("Inherit"),
                  icon: <InheritIcon />,
                },
                ...(models ?? []).map((model) =>
                  modelOption(model, identity.providerName(model.provider_id)),
                ),
              ],
            },
          ]}
          onValueChange={(value) => {
            options.setModel(value === "inherit" ? "" : value);
          }}
        />
      </FormField>
      {("thinking" in properties || thinking !== undefined) && (
        <ChoiceField
          label={t("Thinking effort")}
          value={thinkingValue}
          options={thinkingOptions}
          disabled={invalid}
          onValueChange={(value) => {
            const next = { ...settings };
            if (value === "inherit") delete next.thinking;
            else next.thinking = JSON.parse(value);
            const updated = { ...extra };
            if (Object.keys(next).length) updated.model_settings = next;
            else delete updated.model_settings;
            options.setAdvanced(JSON.stringify(updated, null, 2));
          }}
        />
      )}
      <ErrorNotice error={revision.error ?? definitions.error} />
    </>
  );
}

export function MediaOptions({ options }: { options: RunOptionsState }) {
  const { t } = useTranslation();
  const workspaceDefault = useWorkspaceMediaDefault();
  const summary = useMediaSummary();
  return (
    <DisclosureSection
      title={t("Media understanding")}
      summary={summary(options.mediaUnderstanding, t("Inherit"))}
    >
      <SettingsSection variant="plain">
        <MediaUnderstandingFields
          value={options.mediaUnderstanding}
          explainEmpty={false}
          inherit={{
            label: t("Inherit"),
            describe: (kind) => {
              const model = workspaceDefault(kind);
              return model
                ? t("Agent or workspace default · {{model}}", { model })
                : t("Agent or workspace default");
            },
          }}
          onChange={options.setMediaUnderstanding}
        />
      </SettingsSection>
    </DisclosureSection>
  );
}
