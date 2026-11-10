import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  ChoiceField,
  FormField,
  ModalFrame,
  SearchPicker,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, TextAreaField } from "../../shared/forms";
import { workspaceModelsQuery } from "../models/api";
import { invalidateMemories } from "./api";
import { validHistory } from "./organization-form";
import { OrganizationHistoryField } from "./organization-history-field";
import type { OrganizationTarget } from "./organizations-api";

export function OrganizationRequest({
  target,
  model = "",
  onAccepted,
}: {
  target: OrganizationTarget;
  model?: string;
  onAccepted: (organization: Schema["MemoryOrganization"]) => void;
}) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Organize memory")}
      description={t(
        "Run a focused task over saved files and selected conversations.",
      )}
      size="lg"
      closeLabel={t("Close")}
      trigger={<Button>{t("Organize memory")}</Button>}
    >
      {open && (
        <RequestForm
          target={target}
          initialModel={model}
          onCancel={() => setOpen(false)}
          onAccepted={(result) => {
            onAccepted(result);
            setOpen(false);
          }}
        />
      )}
    </ModalFrame>
  );
}
function RequestForm({
  target,
  initialModel,
  onCancel,
  onAccepted,
}: {
  target: OrganizationTarget;
  initialModel: string;
  onCancel: () => void;
  onAccepted: (organization: Schema["MemoryOrganization"]) => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const [model, setModel] = useState(initialModel),
    [instructions, setInstructions] = useState(""),
    [history, setHistory] = useState<Schema["OrganizationHistory"] | null>(
      null,
    ),
    [output, setOutput] = useState<"candidate" | "direct">("candidate");
  const models = useQuery(workspaceModelsQuery(client, workspace.id));
  const create = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/memory-organizations", {
          body: {
            target,
            model,
            ...(instructions.trim() && { instructions: instructions.trim() }),
            history,
            output,
          },
        })
        .then(data),
    onSuccess: (result) => {
      void invalidateMemories(cache, workspace.id);
      onAccepted(result);
    },
  });
  return (
    <form
      className="grid gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        if (!create.isPending && model && validHistory(history))
          create.mutate();
      }}
    >
      <fieldset
        className="fieldset-reset grid gap-4"
        disabled={create.isPending}
      >
        <FormField label={t("Organizer model")}>
          <SearchPicker
            label={t("Organizer model")}
            value={model}
            onValueChange={setModel}
            placeholder={t("Choose a model…")}
            emptyMessage={t(
              "No models available. Configure a provider and model first.",
            )}
            groups={[
              {
                label: t("Available models"),
                options: (models.data ?? []).map((item) => ({
                  value: item.key,
                  label: item.name,
                  disabled: !item.enabled,
                })),
              },
            ]}
          />
        </FormField>
        <ErrorNotice error={models.error} retry={() => void models.refetch()} />
        <TextAreaField
          label={t("Task instructions (optional)")}
          value={instructions}
          onChange={setInstructions}
          rows={3}
          maxLength={16384}
          hint={t(
            "For example: extract durable project decisions, preserve corrections and update the topic index.",
          )}
        />
        <ChoiceField
          label={t("Output")}
          value={output}
          options={[
            { value: "candidate", label: t("Candidate for review") },
            { value: "direct", label: t("Write directly") },
          ]}
          onValueChange={(value) => setOutput(value as typeof output)}
          description={t(
            output === "candidate"
              ? "The target stays unchanged until you review and apply the candidate."
              : "Committed file edits remain even if the run fails or is cancelled.",
          )}
        />
        <OrganizationHistoryField value={history} onChange={setHistory} />
      </fieldset>
      <ErrorNotice error={create.error} />
      <FormActions
        onCancel={onCancel}
        pending={create.isPending}
        disabled={!model || !validHistory(history)}
        label={t("Start organization")}
      />
    </form>
  );
}
