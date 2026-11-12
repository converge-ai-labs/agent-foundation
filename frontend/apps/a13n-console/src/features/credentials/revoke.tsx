import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ApiError } from "../../service-client";
import { data, ifMatch, rowTag } from "../../shared/api";
import { ConflictNotice } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import shared from "../../shared/shared.module.css";
import { credentialKeys, type Credential } from "./api";
import styles from "./credentials.module.css";

export function RevokeCredential({ credential }: { credential: Credential }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [pending, setPending] = useState(false);
  return (
    <ModalFrame
      title={t("Revoke credential")}
      size="md"
      open={open}
      onOpenChange={setOpen}
      closeLabel={t("Close")}
      description={t(
        "Idle Sessions may retain the old token until next use or expiry. This does not revoke the token at its upstream provider.",
      )}
      trigger={
        <Button type="button" size="sm" variant="outline" disabled={pending}>
          {t("Revoke")}
        </Button>
      }
    >
      {open && (
        <RevokeForm
          credential={credential}
          onClose={() => setOpen(false)}
          onPendingChange={setPending}
        />
      )}
    </ModalFrame>
  );
}

function RevokeForm({
  credential,
  onClose,
  onPendingChange,
}: {
  credential: Credential;
  onClose: () => void;
  onPendingChange: (value: boolean) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  // Background metadata refresh cannot change the version this confirmation reviewed.
  const [basis, setBasis] = useState(credential);
  const keys = credentialKeys(workspace.id),
    http = client.workspace(workspace.id);
  const reload = useMutation({
    mutationFn: () =>
      http
        .GET("/api/v1/egress-credentials/{credential_id}", {
          params: { path: { credential_id: basis.id } },
        })
        .then(data),
    onSuccess: (latest) => {
      setBasis(latest);
      revoke.reset();
      cache.setQueryData(keys.item(latest.id), latest);
    },
  });
  const revoke = useMutation({
    retry: false,
    onMutate: () => onPendingChange(true),
    onSettled: () => onPendingChange(false),
    mutationFn: () =>
      http
        .POST("/api/v1/egress-credentials/{credential_id}/revoke", {
          params: { path: { credential_id: basis.id } },
          headers: ifMatch(rowTag(basis)),
        })
        .then(data),
    onSuccess: (saved) => {
      cache.setQueryData(keys.item(saved.id), saved);
      void cache.invalidateQueries({ queryKey: keys.root });
      onClose();
    },
  });
  const reviewRequired =
    !!revoke.error &&
    (!(revoke.error instanceof ApiError) ||
      revoke.error.status >= 500 ||
      revoke.error.status === 412);
  return (
    <div className={shared.stack}>
      <p className={styles.note}>
        {t(
          "Prevents new use and removes the stored token. Active Sessions remove it asynchronously. This cannot be undone.",
        )}
      </p>
      <div className={styles.summary}>
        <strong>{basis.name}</strong>
        <span>
          {t("Credential version {{version}}", {
            version: basis.credential_version,
          })}
        </span>
      </div>
      {basis.state === "revoked" && (
        <p role="status">
          {t(
            "This credential was revoked. Create a new credential to resume use.",
          )}
        </p>
      )}
      {reviewRequired ? (
        <ConflictNotice
          title={t("Review the current credential")}
          description={t(
            "The result is uncertain or the credential changed. Load its current state before confirming again.",
          )}
          recover={{
            label: t("Load current version"),
            onClick: () => reload.mutate(),
            pending: reload.isPending,
          }}
        />
      ) : (
        <ErrorNotice error={revoke.error} />
      )}
      <ErrorNotice error={reload.error} />
      <div className={styles.actions}>
        <Button type="button" variant="outline" onClick={onClose}>
          {t("Cancel")}
        </Button>
        <Button
          type="button"
          variant="destructive"
          loading={revoke.isPending}
          disabled={
            reviewRequired || reload.isPending || basis.state === "revoked"
          }
          onClick={() => revoke.mutate()}
        >
          {t("Revoke credential")}
        </Button>
      </div>
    </div>
  );
}
