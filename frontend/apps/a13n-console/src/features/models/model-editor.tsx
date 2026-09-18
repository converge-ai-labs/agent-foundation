import {
  useResourceEditorState,
  type ResourceEditorControl,
} from "../../shared/dialogs";
import { ResourceEditorButton } from "../../shared/identity";
import { ModalFrame } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { modelApi, type ModelScope } from "./api";
import { ModelForm } from "./model-form";
import styles from "./models.module.css";

export function ModelEditor({
  scope,
  modelId,
  providerId,
  onSaved,
  controlledOpen,
  onClose,
  finalFocus,
}: {
  scope: ModelScope;
  modelId?: string;
  providerId?: string;
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
      description={
        modelId
          ? undefined
          : t(
              "Choose a provider and model, then set the defaults your agents will use.",
            )
      }
      closeLabel={t("Close")}
    >
      <div className={styles.modelEditor}>
        {open &&
          (modelId && model.isPending ? (
            <Loading variant="form" rows={5} />
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
              onSaved={onSaved}
              close={() => setOpen(false)}
            />
          ))}
      </div>
    </ModalFrame>
  );
}
