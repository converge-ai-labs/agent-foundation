import {
  Badge,
  Button,
  ChoiceField,
  DisclosureSection,
  SettingsSection,
  FormField,
  Input,
  Label,
  ModalFrame,
  Switch,
} from "a13n-ui";

import { SearchPicker } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";

import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/form";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import { jsonObject, validateSettings } from "../../shared/validation";
import { modelApi, type ModelScope } from "./api";

export function ModelEditor({
  scope,
  modelId,
  providerId,
  candidate,
}: {
  scope: ModelScope;
  modelId?: string;
  providerId?: string;
  candidate?: Schema["ModelCandidate"];
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [generation, setGeneration] = useState(0),
    api = modelApi(client, scope);
  const model = useQuery({
    queryKey: ["model", scope.kind, scope.id, modelId],
    enabled: open && !!modelId,
    queryFn: ({ signal }) => api.model(modelId!, signal),
  });
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button
          size={modelId || candidate ? "sm" : "default"}
          variant={modelId ? "ghost" : candidate ? "outline" : "default"}
          type="button"
        >
          {!modelId && <Plus size={14} />}
          {t(modelId ? "Edit" : "Add model")}
        </Button>
      }
      size={"lg"}
      title={t(modelId ? "Edit model" : "Add model")}
      description={t(
        "A stable model key connects your agents to one provider and calling API.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {open &&
        (modelId && model.isPending ? (
          <Loading />
        ) : model.error && !model.data ? (
          <ErrorNotice error={model.error} />
        ) : (
          <ModelForm
            key={generation}
            reload={async () => {
              const result = await model.refetch();
              if (!result.error) setGeneration((value) => value + 1);
            }}
            scope={scope}
            resource={modelId ? model.data : undefined}
            providerId={providerId}
            candidate={candidate}
            close={() => setOpen(false)}
          />
        ))}
    </ModalFrame>
  );
}
function ModelForm({
  scope,
  resource,
  providerId,
  candidate,
  close,
  reload,
}: {
  reload: () => Promise<void>;
  scope: ModelScope;
  resource?: { value: Schema["Model"]; etag?: string };
  providerId?: string;
  candidate?: Schema["ModelCandidate"];
  close: () => void;
}) {
  const [original] = useState(resource),
    client = useClient(),
    { t } = useTranslation(),
    cache = useQueryClient(),
    api = modelApi(client, scope);
  const [name, setName] = useState(
      original?.value.name ?? candidate?.display_name ?? "",
    ),
    [key, setKey] = useState(original?.value.key ?? ""),
    [provider, setProvider] = useState(
      original?.value.provider_id ?? providerId ?? "",
    ),
    [upstream, setUpstream] = useState(
      original?.value.upstream_model ?? candidate?.upstream_model ?? "",
    ),
    [modelApiKey, setModelApiKey] = useState(
      original?.value.model_api ?? candidate?.suggested_model_api ?? "",
    ),
    [settingsText, setSettingsText] = useState(
      JSON.stringify(
        original?.value.settings ?? candidate?.suggested_settings ?? {},
        null,
        2,
      ),
    ),
    [description, setDescription] = useState(original?.value.description ?? ""),
    [enabled, setEnabled] = useState(original?.value.enabled ?? true);
  const providers = useQuery({
    queryKey: ["model-provider-choices", scope.kind, scope.id],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const definitions = useQuery({
    queryKey: ["model-provider-types"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/model-provider-types", { signal }).then(data),
  });
  const definition = definitions.data?.items.find(
    (item) =>
      item.type === providers.data?.find((item) => item.id === provider)?.type,
  );
  const describe = useMutation({
    mutationFn: () =>
      api.describe(provider, {
        upstream_model: upstream,
        model_api: modelApiKey || undefined,
      }),
    onSuccess: (result) => {
      if (!modelApiKey) setModelApiKey(result.suggested_model_api);
    },
  });
  const save = useMutation({
    mutationFn: async () => {
      const settings = jsonObject(settingsText);
      if (describe.data)
        validateSettings(describe.data.settings_schema, settings);
      const body = {
        name,
        upstream_model: upstream,
        model_api: modelApiKey || definition?.default_model_api || "",
        settings,
        description: description || null,
        enabled,
      };
      if (!original)
        return api.createModel({ ...body, key, provider_id: provider });
      if (!original.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return api.updateModel(original.value.id, original.etag, body);
    },
    onSuccess: () => {
      void cache.invalidateQueries();
      close();
    },
  });
  let settings: Record<string, unknown> | undefined;
  try {
    settings = jsonObject(settingsText);
  } catch {
    /* The submit boundary reports incomplete JSON. */
  }
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <div className={styles.twoColumns}>
        <FormField className="min-w-0 w-full" label={t("Name")}>
          <Input
            required={true}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
        <FormField
          className="min-w-0 w-full"
          label={t("Model key")}
          description={t("Agents select this stable key.")}
        >
          <Input
            required={true}
            value={key}
            onChange={(event) => setKey(event.target.value)}
            readOnly={!!original}
          />
        </FormField>
      </div>
      <SettingsSection title={t("Provider")}>
        <div className={`${styles.stack} py-5`}>
          <FormField label={t("Provider")}>
            <SearchPicker
              label={t("Provider")}
              placeholder={t("Choose a provider…")}
              emptyMessage={t("Add a provider first.")}
              value={provider}
              groups={[
                {
                  label: t("Providers"),
                  options:
                    providers.data?.map((item) => ({
                      value: item.id,
                      label: item.name,
                      description: item.type,
                    })) ?? [],
                },
              ]}
              disabled={!!original}
              onValueChange={(value) => {
                setProvider(value);
                setModelApiKey("");
                describe.reset();
              }}
            />
          </FormField>
          <FormField
            className="min-w-0 w-full"
            label={t("Upstream model")}
            description={t(
              "Use the model or deployment identifier accepted by your provider.",
            )}
          >
            <Input
              required={true}
              value={upstream}
              onChange={(event) => {
                setUpstream(event.target.value);
                describe.reset();
              }}
              maxLength={256}
            />
          </FormField>
          <ChoiceField
            placeholder={t("Select API")}
            value={modelApiKey || definition?.default_model_api}
            className="min-w-0"
            onValueChange={(value) => {
              setModelApiKey(value);
              describe.reset();
            }}
            label={t("Calling API")}
            options={
              definition?.supported_model_apis.map((value) => ({
                value,
                label: value,
              })) ?? []
            }
          />
          <Button
            variant="outline"
            disabled={!provider || !upstream}
            loading={describe.isPending}
            onClick={() => describe.mutate()}
            type="button"
          >
            {t("Load model information")}
          </Button>
          <ErrorNotice
            error={describe.error ?? providers.error ?? definitions.error}
          />
          {describe.data && (
            <>
              <div className={styles.actions}>
                <Badge variant={"secondary"}>
                  {t("Context window")}:{" "}
                  {describe.data.limits?.context_window_tokens?.toLocaleString() ??
                    t("Unknown")}
                </Badge>
                <Badge variant={"secondary"}>
                  {t("Max output")}:{" "}
                  {describe.data.limits?.max_output_tokens?.toLocaleString() ??
                    t("Unknown")}
                </Badge>
              </div>
              {settings && (
                <SchemaFields
                  schema={describe.data.settings_schema}
                  value={settings}
                  onChange={(value) =>
                    setSettingsText(JSON.stringify(value, null, 2))
                  }
                />
              )}
            </>
          )}
        </div>
      </SettingsSection>
      <DisclosureSection title={<>{t("Advanced model settings")}</>}>
        <TextAreaField
          code
          label={t("Settings JSON")}
          value={settingsText}
          onChange={setSettingsText}
          rows={6}
        />
      </DisclosureSection>
      <FormField className="min-w-0 w-full" label={t("Description")}>
        <Input
          value={description}
          onChange={(event) => setDescription(event.target.value)}
        />
      </FormField>
      <Label className="flex items-center gap-2">
        <Switch checked={enabled} onCheckedChange={setEnabled} />
        {t("Enabled")}
      </Label>
      <ErrorNotice
        error={save.error}
        retry={original ? () => void reload() : undefined}
      />
      <FormActions pending={save.isPending} />
    </form>
  );
}
export function ModelTest({
  scope,
  modelId,
}: {
  scope: ModelScope;
  modelId: string;
}) {
  const { t } = useTranslation(),
    api = modelApi(useClient(), scope);
  const test = useMutation({ mutationFn: () => api.testModel(modelId) });
  return (
    <ModalFrame
      trigger={
        <Button size="sm" variant="ghost" type="button">
          {t("Test")}
        </Button>
      }
      size={"md"}
      title={t("Test model")}
      description={t(
        "This makes a model request and may consume quota or incur cost.",
      )}
      closeLabel={t("Close")}
    >
      <Button
        variant="outline"
        loading={test.isPending}
        onClick={() => test.mutate()}
        type="button"
      >
        {t("Run model test")}
      </Button>
      <ErrorNotice error={test.error} />
      {test.data && (
        <p role="status">
          <StateBadge state={test.data.success ? "succeeded" : "failed"} />{" "}
          {test.data.message} · {test.data.elapsed_ms} ms
        </p>
      )}
    </ModalFrame>
  );
}
