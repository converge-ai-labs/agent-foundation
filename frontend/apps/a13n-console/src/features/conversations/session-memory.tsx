import { BrainIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import {
  defaultSessionMemory,
  SessionMemoryFields,
  validSpaceSelection,
} from "../memories/session-settings";
import { conversationQueries, invalidateConversation } from "./api";

type Settings = Schema["SessionMemorySettings"];

export function SessionMemory({ sessionId }: { sessionId: string }) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Session memory")}
      description={t(
        "Choose shared Spaces for this session. Changes apply to new runs.",
      )}
      closeLabel={t("Close")}
      size="lg"
      trigger={
        <Button variant="ghost" size="sm">
          <BrainIcon size={15} />
          {t("Memory")}
        </Button>
      }
    >
      {open && (
        <MemoryContent sessionId={sessionId} onClose={() => setOpen(false)} />
      )}
    </ModalFrame>
  );
}

function MemoryContent({
  sessionId,
  onClose,
}: {
  sessionId: string;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const cache = useQueryClient();
  const { workspace, can } = useWorkspace();
  const session = useQuery(
    conversationQueries(client, workspace.id).session(sessionId),
  );
  const [draft, setDraft] = useState<{ value: Settings; tag: string }>();
  const save = useMutation({
    mutationFn: (memory: Settings) =>
      client
        .workspace(workspace.id)
        .PATCH("/api/v1/sessions/{session_id}", {
          params: { path: { session_id: sessionId } },
          headers: ifMatch(draft?.tag),
          body: { memory },
        })
        .then(data),
    onSuccess: () => {
      setDraft(undefined);
      void invalidateConversation(cache, workspace.id, { sessionId });
      onClose();
    },
  });
  const owner = session.data;
  if (!owner)
    return (
      <>
        <ErrorNotice error={session.error} />
        <Loading variant="form" />
      </>
    );
  const value = { ...defaultSessionMemory, ...owner.memory, ...draft?.value };
  return (
    <form
      className="grid gap-6"
      onSubmit={(event) => {
        event.preventDefault();
        if (draft) save.mutate(draft.value);
      }}
    >
      <SessionMemoryFields
        value={value}
        onChange={(value, field) =>
          setDraft({
            value: { ...draft?.value, [field]: value[field] },
            tag: draft?.tag ?? rowTag(owner),
          })
        }
        disabled={!can("run") || save.isPending}
      />
      <ErrorNotice
        error={save.error ?? session.error}
        retry={() => void session.refetch()}
      />
      <FormActions
        dismiss={!can("run")}
        onCancel={onClose}
        pending={save.isPending}
        disabled={!draft || !can("run") || !validSpaceSelection(value)}
        label={t("Save")}
      />
    </form>
  );
}
