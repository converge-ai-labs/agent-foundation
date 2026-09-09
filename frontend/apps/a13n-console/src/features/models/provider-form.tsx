import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChoiceField, FormField, Input, Label, Switch } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import { SchemaFields } from "../../shared/schema-fields";
import styles from "../../shared/shared.module.css";
import { validateSettings } from "../../shared/validation";
import { modelApi, type ModelScope } from "./api";

export function ProviderForm({
  scope,
  resource,
  definitions,
  close,
  reload,
}: {
  reload: () => Promise<void>;
  scope: ModelScope;
  resource?: { value: Schema["ModelProvider"]; etag?: string };
  definitions: Schema["ModelProviderDefinition"][];
  close: () => void;
}) {
  const [original] = useState(resource),
    { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    api = modelApi(client, scope);
  const [type, setType] = useState(
      original?.value.type ?? definitions[0]?.type ?? "",
    ),
    [name, setName] = useState(original?.value.name ?? ""),
    [configuration, setConfiguration] = useState<Record<string, unknown>>(
      original?.value.configuration ?? {},
    ),
    [credential, setCredential] = useState(""),
    [removeCredential, setRemoveCredential] = useState(false),
    [enabled, setEnabled] = useState(original?.value.enabled ?? true);
  const definition = definitions.find((item) => item.type === type);
  const save = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      if (!definition) throw new Error(t("Choose a provider type."));
      validateSettings(definition.configuration_schema, configuration);
      const body = {
        name,
        configuration,
        enabled,
        ...(removeCredential
          ? { credential: null }
          : credential
            ? { credential }
            : {}),
      };
      if (!original) return api.createProvider({ ...body, type });
      if (!original.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return api.updateProvider(original.value.id, original.etag, body);
    },
    onSuccess: () => {
      setCredential("");
      void cache.invalidateQueries();
      close();
    },
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormField className="min-w-0 w-full" label={t("Name")}>
        <Input
          required={true}
          value={name}
          onChange={(event) => setName(event.target.value)}
          maxLength={128}
        />
      </FormField>
      <ChoiceField
        placeholder={t("Choose a provider…")}
        value={type}
        className="min-w-0"
        disabled={!!original}
        onValueChange={(value) => {
          setType(value);
          setConfiguration({});
          setCredential("");
        }}
        label={t("Provider type")}
        options={definitions.map((item) => ({
          value: item.type,
          label: item.display_name,
        }))}
      />
      {definition && (
        <SchemaFields
          key={type}
          schema={definition.configuration_schema}
          value={configuration}
          onChange={setConfiguration}
        />
      )}
      <FormField
        className="min-w-0 w-full"
        label={t("Credential")}
        description={t(
          original?.value.credential_configured
            ? "Leave empty to keep the current credential."
            : "Enter the credential required by this provider.",
        )}
      >
        <Input
          type="password"
          autoComplete="off"
          value={credential}
          onChange={(event) => {
            setCredential(event.target.value);
            setRemoveCredential(false);
          }}
        />
      </FormField>
      {original?.value.credential_configured && (
        <Label className="flex items-center gap-2">
          <Switch
            checked={removeCredential}
            onCheckedChange={setRemoveCredential}
          />
          {t("Remove stored credential")}
        </Label>
      )}
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
