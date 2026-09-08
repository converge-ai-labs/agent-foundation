import { useState } from "react";
import { useNavigate } from "react-router";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input } from "a13n-ui";
import { Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";

export function CreateWorkspace({
  organizationId,
}: {
  organizationId: string;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    cache = useQueryClient(),
    navigate = useNavigate();
  const [open, setOpen] = useState(false),
    [name, setName] = useState("");
  const create = useMutation({
    mutationFn: () =>
      client.http
        .POST("/api/v1/organizations/{organization_id}/workspaces", {
          params: { path: { organization_id: organizationId } },
          body: { name },
        })
        .then(data),
    onSuccess: (result) => {
      void cache.invalidateQueries({
        queryKey: ["workspaces", organizationId],
      });
      setOpen(false);
      setName("");
      navigate(`/workspaces/${result.id}/settings`);
    },
  });
  return (
    <Dialog
      title={t("Create workspace")}
      description={t("Choose a name your teammates will recognize.")}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={(value) => {
        if (!create.isPending) setOpen(value);
      }}
      trigger={
        <Button variant="primary" icon={<Plus size={14} />}>
          {t("Create workspace")}
        </Button>
      }
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <Input
          label={t("Workspace name")}
          value={name}
          onChange={(event) => setName(event.target.value)}
          required
          maxLength={128}
          disabled={create.isPending}
        />
        <ErrorNotice error={create.error} />
        <FormActions pending={create.isPending} label={t("Create workspace")} />
      </form>
    </Dialog>
  );
}
