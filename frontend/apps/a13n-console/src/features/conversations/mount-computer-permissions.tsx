import { useState } from "react";
import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import { invalidateConversation } from "./api";

import {
  computerActions,
  observationActions,
  computerPermissionLabels as labels,
  computerPermissionMode as modeOf,
} from "./computer-permissions";
type Mode = keyof typeof labels;

/** These are Agent action ceilings for later Runs, not human desktop control. */
export function MountComputerPermissions({
  thread,
  mount,
  editable,
}: {
  thread: Schema["ThreadView"];
  mount: Schema["MountView"];
  editable: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const current = modeOf(mount.permission_ceiling?.operations ?? []);
  if (!editable)
    return (
      <span className="text-xs text-muted-foreground">
        {t(labels[current])}
      </span>
    );
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Computer permissions")}
      description={t(
        "Allow this Agent to observe or interact with the environment's desktop. Its Computer toolset must also be enabled, and the environment must support desktop operations. Changes apply to later runs.",
      )}
      closeLabel={t("Close")}
      trigger={
        <Button type="button" variant="outline" size="sm">
          {t(labels[current])}
        </Button>
      }
    >
      {open && (
        <PermissionsForm
          thread={thread}
          mount={mount}
          current={current}
          close={() => setOpen(false)}
        />
      )}
    </ModalFrame>
  );
}

function PermissionsForm({
  thread,
  mount,
  current,
  close,
}: {
  thread: Schema["ThreadView"];
  mount: Schema["MountView"];
  current: Mode;
  close: () => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const cache = useQueryClient();
  const [mode, setMode] = useState<Mode>(current);
  const save = useMutation({
    mutationFn: () => {
      const existing = mount.permission_ceiling?.operations ?? [];
      const operations =
        mode === "custom"
          ? existing
          : [
              ...existing.filter(
                (value) => !value.startsWith("environment.computer."),
              ),
              ...(mode === "interact"
                ? computerActions
                : mode === "observe"
                  ? observationActions
                  : []),
            ];
      return client
        .workspace(workspace.id)
        .PATCH("/api/v1/threads/{thread_id}/environments/{name}", {
          params: { path: { thread_id: thread.id, name: mount.name } },
          headers: ifMatch(rowTag(thread)),
          body: { permission_ceiling: { operations } },
        })
        .then(data);
    },
    onSuccess: () => {
      void invalidateConversation(cache, workspace.id, {
        sessionId: thread.session_id,
        threadId: thread.id,
      });
      close();
    },
  });
  return (
    <form
      className="flex flex-col gap-4"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <ChoiceField
        label={t("Computer permissions")}
        value={mode}
        onValueChange={(value) => setMode(value as Mode)}
        options={Object.entries(labels)
          .filter(([value]) => value !== "custom" || current === "custom")
          .map(([value, label]) => ({ value, label: t(label) }))}
      />
      <p className="text-sm text-muted-foreground">
        {t(
          "This controls Computer tools only. Terminal commands and other tools keep their existing permissions.",
        )}
      </p>
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        onCancel={close}
        label={t("Save")}
      />
    </form>
  );
}
