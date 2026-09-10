import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/resource-modal";
import { ResourceEditorButton } from "../../shared/resource-editor-button";
import { ModalFrame } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { modelApi, type ModelScope } from "./api";
import { ModelForm } from "./model-form";

export function ModelEditor({
  scope,
  modelId,
  providerId,
  candidate,
  onSaved,
  controlledOpen,
  onClose,
  finalFocus,
}: {
  scope: ModelScope;
  modelId?: string;
  providerId?: string;
  candidate?: Schema["ModelCandidate"];
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
    enabled: open && !!modelId,
    queryFn: ({ signal }) => api.model(modelId!, signal),
  });
  return (
    <ModalFrame
      {...modalProps}
      trigger={
        controlledOpen === undefined ? (
          <ResourceEditorButton
            editing={!!modelId}
            createLabel="Add model"
            editLabel="Edit"
          />
        ) : undefined
      }
      size={"lg"}
      title={t(modelId ? "Edit model" : "Add model")}
      closeLabel={t("Close")}
    >
      {open &&
        (modelId && model.isPending ? (
          <Loading />
        ) : model.error && !model.data ? (
          <ErrorNotice error={model.error} />
        ) : (
          <ModelForm
            key={generation}
            reload={async () => {
              const result = await model.refetch();
              if (!result.error) setGeneration((value) => value + 1);
            }}
            scope={scope}
            resource={modelId ? model.data : undefined}
            providerId={providerId}
            candidate={candidate}
            onSaved={onSaved}
            close={() => setOpen(false)}
          />
        ))}
    </ModalFrame>
  );
}
