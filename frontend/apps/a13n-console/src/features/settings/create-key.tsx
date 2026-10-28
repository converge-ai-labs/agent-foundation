import { useMutation, useQueryClient } from "@tanstack/react-query";
import { ChoiceField, FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import {
  expirationOptions,
  expirationTimestamp,
  type Expiration,
} from "./expiration";

/** Deliver the one-time secret to the mounted caller, never the mutation cache. */
export function useCreateKey(
  onCreated: (secret: string) => void,
  accountId?: string,
) {
  const client = useClient();
  const { workspace } = useWorkspace();
  const cache = useQueryClient();
  return useMutation({
    gcTime: 0,
    mutationFn: async ({
      name,
      expires,
    }: {
      name: string;
      expires: Expiration;
    }) => {
      const body = { name, expires_at: expirationTimestamp(expires) };
      const created = accountId
        ? await client.http
            .POST(
              "/api/v1/workspaces/{workspace_id}/service-accounts/{account_id}/keys",
              {
                params: {
                  path: { workspace_id: workspace.id, account_id: accountId },
                },
                body,
              },
            )
            .then(data)
        : await client.http
            .POST("/api/v1/users/me/keys", {
              body: { ...body, workspace_id: workspace.id },
            })
            .then(data);
      onCreated(created.secret);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["keys", workspace.id] });
    },
  });
}

/** Key management exposes explicit name and expiration controls. */
export function CreateKeyForm({
  create,
  onCancel,
}: {
  create: ReturnType<typeof useCreateKey>;
  onCancel?: () => void;
}) {
  const { t } = useTranslation();
  const [name, setName] = useState("");
  const [expires, setExpires] = useState<Expiration>("never");
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate({ name: name.trim(), expires });
      }}
    >
      <div className="grid gap-3 sm:grid-cols-[minmax(0,1fr)_10rem]">
        <FormField className="min-w-0 w-full" label={t("Name")}>
          <Input
            required
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={128}
            autoFocus
            disabled={create.isPending}
          />
        </FormField>
        <ChoiceField
          placeholder={t("Select expiration")}
          value={expires}
          className="min-w-0"
          disabled={create.isPending}
          onValueChange={(value) => {
            const option = expirationOptions.find(
              (option) => option.value === value,
            );
            if (option) setExpires(option.value);
          }}
          label={t("Expires after")}
          options={expirationOptions.map((option) => ({
            ...option,
            label: t(option.label),
          }))}
        />
      </div>
      <ErrorNotice error={create.error} />
      <FormActions
        onCancel={onCancel}
        pending={create.isPending}
        label={t("Create key")}
        disabled={!name.trim()}
      />
    </form>
  );
}
