import { Button, FormField, Input, ModalFrame } from "a13n-ui";

import { useMutation, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useNavigate } from "react-router";

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
    <ModalFrame
      onOpenChange={(value) => {
        if (!create.isPending) setOpen(value);
      }}
      trigger={
        <Button variant="default" type="button">
          {<Plus size={14} />}
          {t("Create workspace")}
        </Button>
      }
      size={"md"}
      title={t("Create workspace")}
      description={t("Choose a name your teammates will recognize.")}
      closeLabel={t("Close")}
      open={open}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          create.mutate();
        }}
      >
        <FormField
          className="min-w-0 w-full"
          label={t("Workspace name")}
          disabled={create.isPending}
        >
          <Input
            required={true}
            value={name}
            onChange={(event) => setName(event.target.value)}
            maxLength={128}
          />
        </FormField>
        <ErrorNotice error={create.error} />
        <FormActions pending={create.isPending} label={t("Create workspace")} />
      </form>
    </ModalFrame>
  );
}
