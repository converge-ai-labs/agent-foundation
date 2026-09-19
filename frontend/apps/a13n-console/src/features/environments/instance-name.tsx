import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import styles from "./environments.module.css";

/**
 * Renaming keeps the precondition it opened with: a draft survives a
 * concurrent change until the author reloads on purpose.
 */
export function EnvironmentNameEditor({
  environment,
  etag,
  reload,
  onCancel,
}: {
  environment: Pick<Schema["Environment"], "id" | "name">;
  etag?: string;
  reload: () => Promise<void>;
  onCancel?: () => void;
}) {
  const client = useClient();
  const cache = useQueryClient();
  const { t } = useTranslation();
  const [basis] = useState({ environment, etag });
  const [name, setName] = useState(basis.environment.name);
  const save = useMutation({
    mutationFn: async () => {
      if (!basis.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      return client.http
        .PATCH("/api/v1/environments/{environment_id}", {
          params: {
            path: { environment_id: basis.environment.id },
            header: { "If-Match": basis.etag },
          },
          body: { name },
        })
        .then(data);
    },
    onSuccess: async () => {
      void cache.invalidateQueries({ queryKey: ["environments"] });
      void cache.invalidateQueries({ queryKey: ["run-options"] });
      await reload();
    },
  });
  return (
    <form
      className={styles.rename}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <FormField label={t("Name")}>
        <Input
          required
          maxLength={128}
          value={name}
          onChange={(event) => setName(event.target.value)}
        />
      </FormField>
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <div className={styles.renameActions}>
        {onCancel && (
          <Button
            type="button"
            size="sm"
            variant="ghost"
            onClick={onCancel}
            disabled={save.isPending}
          >
            {t("Cancel")}
          </Button>
        )}
        <Button
          type="submit"
          size="sm"
          variant="outline"
          loading={save.isPending}
          disabled={name === basis.environment.name}
        >
          {t("Save name")}
        </Button>
      </div>
    </form>
  );
}
