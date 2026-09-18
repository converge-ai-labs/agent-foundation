import { useMutation, useQueryClient } from "@tanstack/react-query";
import {} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { commandHeaders, data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { useIdempotency } from "../../shared/idempotency";
import { SchemaFields } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import { stringValues, validateSettings } from "../../shared/forms";
import { useAccountProviders } from "./data";
export function AccountCredentials({
  account,
  reload,
}: {
  account: Schema["Account"];
  reload: () => Promise<void>;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    key = useIdempotency(),
    definitions = useAccountProviders(),
    [basis] = useState(account),
    [credentials, setCredentials] = useState<Record<string, unknown>>({});
  const definition = definitions.data?.items.find(
    (item) =>
      item.provider_key === basis.provider_key &&
      item.config_version === basis.provider_config_version,
  );
  const save = useMutation({
    gcTime: 0,
    mutationFn: () => {
      if (!definition) throw new Error(t("Account provider unavailable."));
      validateSettings(definition.credential_schema, credentials);
      const body = {
        credentials: stringValues(credentials),
        expected_version: basis.version,
      };
      return client.http
        .PUT("/api/v1/application-accounts/{account_id}/credentials", {
          params: {
            path: { account_id: basis.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: () => {
      setCredentials({});
      void cache.invalidateQueries({ queryKey: ["application-accounts"] });
      void reload();
    },
  });
  return definitions.isPending ? (
    <Loading variant="form" rows={3} />
  ) : (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ErrorNotice error={definitions.error} />
      {definition && (
        <SchemaFields
          schema={definition.credential_schema}
          value={credentials}
          onChange={setCredentials}
          secret
        />
      )}
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <FormActions pending={save.isPending} label={t("Replace credentials")} />
    </form>
  );
}
