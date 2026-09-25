import { PlusIcon } from "@phosphor-icons/react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import {
  FormActions,
  TextAreaField,
  useSuggestedName,
} from "../../shared/forms";
import { readableKey } from "../../shared/keys";
import { resourceKeyPattern } from "../../shared/paths";
import shared from "../../shared/shared.module.css";
import { invalidateMemories } from "./api";
import { AlwaysLoadField, GuideField } from "./fields";
import { memoryCreate, memoryDraft } from "./form";

export function CreateMemory({
  onCreated,
}: {
  onCreated: (memory: Schema["Memory"]) => void;
}) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button variant="default" type="button">
          <PlusIcon size={14} />
          {t("Create memory")}
        </Button>
      }
      size="lg"
      title={t("Create memory")}
      description={t(
        "A memory holds files that agents read and write across conversations.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {open && (
        <CreateForm
          onCancel={() => setOpen(false)}
          onCreated={(memory) => {
            setOpen(false);
            onCreated(memory);
          }}
        />
      )}
    </ModalFrame>
  );
}

function CreateForm({
  onCreated,
  onCancel,
}: {
  onCreated: (memory: Schema["Memory"]) => void;
  onCancel: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const [draft, setDraft] = useState(memoryDraft);
  const key = useSuggestedName();
  const create = useMutation({
    mutationFn: () =>
      client.http
        .POST("/api/v1/workspaces/{workspace_id}/memories", {
          params: { path: { workspace_id: workspace.id } },
          body: memoryCreate(key.name, draft),
        })
        .then(data),
    onSuccess: (memory) => {
      void invalidateMemories(cache, workspace.id);
      onCreated(memory);
    },
  });
  return (
    <form
      className={shared.form}
      onSubmit={(event) => {
        event.preventDefault();
        create.mutate();
      }}
    >
      <FormField label={t("Name")} disabled={create.isPending}>
        <Input
          required
          value={draft.name}
          maxLength={128}
          onChange={(event) => {
            setDraft({ ...draft, name: event.target.value });
            key.suggestName(readableKey(event.target.value));
          }}
        />
      </FormField>
      <FormField
        label={t("Key")}
        description={t("Unique in this workspace. It cannot change later.")}
        disabled={create.isPending}
      >
        <Input
          required
          value={key.name}
          pattern={resourceKeyPattern}
          maxLength={64}
          autoComplete="off"
          onChange={(event) => key.setName(event.target.value)}
        />
      </FormField>
      <TextAreaField
        label={t("Description")}
        rows={2}
        value={draft.description}
        onChange={(description) => setDraft({ ...draft, description })}
      />
      <GuideField
        value={draft.guide}
        onChange={(guide) => setDraft({ ...draft, guide })}
      />
      <div className="grid gap-2">
        <span className="text-[13px] font-medium">
          {t("Always loaded files")}
        </span>
        <AlwaysLoadField
          value={draft.alwaysLoad}
          onChange={(alwaysLoad) => setDraft({ ...draft, alwaysLoad })}
        />
      </div>
      <ErrorNotice error={create.error} />
      <FormActions
        onCancel={onCancel}
        pending={create.isPending}
        label={t("Create memory")}
      />
    </form>
  );
}
