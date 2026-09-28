import { ModalFrame } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { type Schema } from "../../shared/api";
import {
  ResourceModalTitle,
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { AddModel } from "./add-model";
import { modelApi } from "./api";
import { EditModelForm } from "./model-form";

/** Adding a model walks through steps; editing one shows every section at once. */
export function ModelEditor({
  modelKey,
  providerId,
  onSaved,
  ...control
}: {
  modelKey?: string;
  providerId?: string;
  onSaved?: (model: Schema["Model"]) => void;
} & ResourceEditorControl) {
  return modelKey ? (
    <EditModel modelKey={modelKey} onSaved={onSaved} {...control} />
  ) : (
    <AddModel providerId={providerId} onSaved={onSaved} {...control} />
  );
}

function EditModel({
  modelKey,
  onSaved,
  controlledOpen,
  onClose,
  finalFocus,
}: {
  modelKey: string;
  onSaved?: (model: Schema["Model"]) => void;
} & ResourceEditorControl) {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace } = useWorkspace(),
    [generation, setGeneration] = useState(0),
    api = modelApi(client, workspace.id);
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });
  const model = useQuery({
    queryKey: ["model", workspace.id, modelKey],
    enabled: open,
    queryFn: ({ signal }) => api.model(modelKey, signal),
  });
  return (
    <ModalFrame
      {...modalProps}
      size="lg"
      placement="top"
      title={
        model.data ? (
          <ResourceModalTitle
            name={model.data.value.name}
            resourceKey={model.data.value.key}
          />
        ) : (
          t("Edit model")
        )
      }
      closeLabel={t("Close")}
    >
      {open &&
        (model.isPending ? (
          <Loading variant="form" rows={5} />
        ) : model.error && !model.data ? (
          <ErrorNotice error={model.error} />
        ) : (
          <EditModelForm
            key={generation}
            resource={model.data}
            onSaved={onSaved}
            reload={async () => {
              const result = await model.refetch();
              if (!result.error) setGeneration((value) => value + 1);
            }}
            close={() => setOpen(false)}
          />
        ))}
    </ModalFrame>
  );
}
