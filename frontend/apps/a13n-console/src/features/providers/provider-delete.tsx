import { Button, ModalFrame } from "a13n-ui";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ifMatch, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import type { ProviderApiKind } from "./api";
import { EnvironmentConnectionDelete } from "./environment-connection-delete";

export function ProviderDelete(props: {
  kind: ProviderApiKind;
  provider: Schema["Provider"];
  etag?: string;
  disabled?: boolean;
  onDeleted: () => void;
}) {
  return props.kind === "environment" ? (
    <EnvironmentConnectionDelete {...props} />
  ) : (
    <DeleteConfirmation {...props} />
  );
}

function DeleteConfirmation({
  kind,
  provider,
  etag,
  disabled,
  onDeleted,
}: Parameters<typeof ProviderDelete>[0]) {
  const { t } = useTranslation();
  const { workspace } = useWorkspace();
  const http = useClient().workspace(workspace.id);
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const deletion = useMutation({
    mutationFn: () =>
      http.DELETE(`/api/v1/${kind}-providers/{provider_id}`, {
        params: { path: { provider_id: provider.id } },
        headers: ifMatch(etag),
      }),
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: [`${kind}-providers`] });
      void cache.invalidateQueries({ queryKey: [`${kind}-provider`] });
      onDeleted();
    },
  });
  return (
    <ModalFrame
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!deletion.isPending) deletion.reset();
      }}
      title={t("Delete provider")}
      description={provider.name}
      closeLabel={t("Close")}
      size="md"
      trigger={
        <Button type="button" variant="destructive" disabled={disabled}>
          {t("Delete")}
        </Button>
      }
      footer={
        <>
          <Button
            type="button"
            variant="outline"
            disabled={deletion.isPending}
            onClick={() => setOpen(false)}
          >
            {t("Cancel")}
          </Button>
          <Button
            type="button"
            variant="destructive"
            loading={deletion.isPending}
            onClick={() => deletion.mutate()}
          >
            {t("Delete provider")}
          </Button>
        </>
      }
    >
      <p>
        {t(
          "Deleting this provider permanently removes its configuration and saved credentials. Providers that are still referenced cannot be deleted.",
        )}
      </p>
      <ErrorNotice error={deletion.error} />
    </ModalFrame>
  );
}
