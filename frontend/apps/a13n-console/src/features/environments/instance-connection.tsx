import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, ifMatch, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import styles from "./environments.module.css";

/**
 * Points an external target at a new endpoint or token. The Service keeps
 * them only once they reach the same device, and a new endpoint always comes
 * with its token, so the saved token is never sent anywhere new.
 */
export function EnvironmentConnectionEditor({
  environment,
  etag,
  reload,
  onClose,
}: {
  environment: Pick<
    Schema["EnvironmentView"],
    "id" | "endpoint" | "workspace_id"
  >;
  etag?: string;
  reload: () => Promise<void>;
  onClose: () => void;
}) {
  const client = useClient();
  const cache = useQueryClient();
  const { t } = useTranslation();
  const [basis] = useState({ environment, etag });
  const [endpoint, setEndpoint] = useState(basis.environment.endpoint ?? "");
  const [token, setToken] = useState("");
  const save = useMutation({
    mutationFn: async () => {
      if (!basis.etag)
        throw new Error(
          t("Version information is unavailable. Reload this page."),
        );
      const moved = endpoint.trim() !== basis.environment.endpoint;
      return client
        .workspace(basis.environment.workspace_id)
        .PATCH("/api/v1/environments/{environment_id}", {
          params: {
            path: {
              environment_id: basis.environment.id,
            },
          },
          headers: ifMatch(basis.etag),
          body: { token, ...(moved && { endpoint: endpoint.trim() }) },
        })
        .then(data);
    },
    onSuccess: async () => {
      void cache.invalidateQueries({ queryKey: ["environments"] });
      await reload();
      onClose();
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
      <FormField label={t("Endpoint URL")}>
        <Input
          required
          type="url"
          value={endpoint}
          onChange={(event) => setEndpoint(event.target.value)}
        />
      </FormField>
      <FormField
        label={t("Token")}
        description={t(
          "The saved token is never shown. Enter it again, or the new one, to save the connection.",
        )}
      >
        <Input
          required
          type="password"
          autoComplete="off"
          value={token}
          onChange={(event) => setToken(event.target.value)}
        />
      </FormField>
      <ErrorNotice error={save.error} retry={() => void reload()} />
      <div className={styles.renameActions}>
        <Button
          type="button"
          size="sm"
          variant="ghost"
          onClick={onClose}
          disabled={save.isPending}
        >
          {t("Cancel")}
        </Button>
        <Button
          type="submit"
          size="sm"
          variant="outline"
          loading={save.isPending}
          disabled={!token}
        >
          {t("Save connection")}
        </Button>
      </div>
    </form>
  );
}
