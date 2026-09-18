import { ModalFrame } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { type Schema } from "../../shared/api";
import {
  ResourceModalTitle,
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { AddModel } from "./add-model";
import { modelApi, type ModelScope } from "./api";
import { EditModelForm } from "./model-form";

/** Adding a model walks through steps; editing one shows every section at once. */
export function ModelEditor({
  scope,
  modelId,
  providerId,
  onSaved,
  ...control
}: {
  scope: ModelScope;
  modelId?: string;
  providerId?: string;
  onSaved?: (model: Schema["Model"]) => void;
} & ResourceEditorControl) {
  return modelId ? (
    <EditModel scope={scope} modelId={modelId} onSaved={onSaved} {...control} />
  ) : (
    <AddModel
      scope={scope}
      providerId={providerId}
      onSaved={onSaved}
      {...control}
    />
  );
}

function EditModel({
  scope,
  modelId,
  onSaved,
  controlledOpen,
  onClose,
  finalFocus,
}: {
  scope: ModelScope;
  modelId: string;
  onSaved?: (model: Schema["Model"]) => void;
} & ResourceEditorControl) {
  const client = useClient(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0),
    api = modelApi(client, scope);
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });
  const model = useQuery({
    queryKey: ["model", scope.kind, scope.id, modelId],
    enabled: open,
    queryFn: ({ signal }) => api.model(modelId, signal),
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
            id={model.data.value.id}
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
            scope={scope}
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
