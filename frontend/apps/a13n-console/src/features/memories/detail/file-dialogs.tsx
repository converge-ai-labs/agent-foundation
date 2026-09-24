import { useMutation, useQueryClient } from "@tanstack/react-query";
import { FormField, Input, ModalFrame } from "a13n-ui";
import { useState, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, ifMatch, type Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import { FormActions, TextAreaField } from "../../../shared/forms";
import shared from "../../../shared/shared.module.css";
import { invalidateMemories, isStale } from "../api";

type Memory = Schema["Memory"];

/** A dialog whose form mounts fresh each time it opens. */
function FileDialog({
  trigger,
  title,
  description,
  children,
}: {
  trigger: ReactElement;
  title: string;
  description: string;
  children: (close: () => void) => ReactElement;
}) {
  const { t } = useTranslation(),
    [open, setOpen] = useState(false);
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={trigger}
      size="lg"
      title={title}
      description={description}
      closeLabel={t("Close")}
      open={open}
    >
      {open && children(() => setOpen(false))}
    </ModalFrame>
  );
}

export function NewFile({
  memory,
  trigger,
  onCreated,
}: {
  memory: Memory;
  trigger: ReactElement;
  onCreated: (path: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <FileDialog
      trigger={trigger}
      title={t("New file")}
      description={t(
        "Agents see the file the next time a run starts or lists this memory.",
      )}
    >
      {(close) => (
        <NewFileForm
          memory={memory}
          onCancel={close}
          onCreated={(path) => {
            close();
            onCreated(path);
          }}
        />
      )}
    </FileDialog>
  );
}

function NewFileForm({
  memory,
  onCreated,
  onCancel,
}: {
  memory: Memory;
  onCreated: (path: string) => void;
  onCancel: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const [path, setPath] = useState(""),
    [content, setContent] = useState("");
  const create = useMutation({
    mutationFn: () =>
      client.http
        .POST("/api/v1/workspaces/{workspace_id}/memories/{memory_id}/files", {
          params: {
            path: { workspace_id: workspace.id, memory_id: memory.id },
          },
          body: { path: path.trim(), content },
        })
        .then(data),
    onSuccess: (file) => {
      void invalidateMemories(cache, workspace.id);
      onCreated(file.path);
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
      <FormField
        label={t("Path")}
        description={t("Folders are part of the path, such as notes/topic.md.")}
        disabled={create.isPending}
      >
        <Input
          required
          value={path}
          autoComplete="off"
          placeholder="notes/topic.md"
          onChange={(event) => setPath(event.target.value)}
        />
      </FormField>
      <TextAreaField
        label={t("Content")}
        code
        rows={10}
        value={content}
        onChange={setContent}
      />
      <ErrorNotice error={create.error} />
      <FormActions
        onCancel={onCancel}
        pending={create.isPending}
        label={t("Create file")}
      />
    </form>
  );
}

export function RenameFile({
  memory,
  path,
  etag,
  trigger,
  onMoved,
}: {
  memory: Memory;
  path: string;
  /** The file as the reader saw it; a file changed since is not moved. */
  etag?: string;
  trigger: ReactElement;
  onMoved: (path: string) => void;
}) {
  const { t } = useTranslation();
  return (
    <FileDialog
      trigger={trigger}
      title={t("Rename file")}
      description={t(
        "The file keeps its history. Always-loaded paths are not updated.",
      )}
    >
      {(close) => (
        <RenameForm
          memory={memory}
          path={path}
          etag={etag}
          onCancel={close}
          onMoved={(destination) => {
            close();
            onMoved(destination);
          }}
        />
      )}
    </FileDialog>
  );
}

function RenameForm({
  memory,
  path,
  etag,
  onMoved,
  onCancel,
}: {
  memory: Memory;
  path: string;
  etag?: string;
  onMoved: (path: string) => void;
  onCancel: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const [destination, setDestination] = useState(path);
  const move = useMutation({
    mutationFn: () =>
      client.http
        .POST(
          "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/files/move",
          {
            params: {
              path: { workspace_id: workspace.id, memory_id: memory.id },
            },
            headers: ifMatch(etag),
            body: { source: path, destination: destination.trim() },
          },
        )
        .then(data),
    onSuccess: (file) => {
      void invalidateMemories(cache, workspace.id);
      onMoved(file.path);
    },
  });
  const next = destination.trim();
  return (
    <form
      className={shared.form}
      onSubmit={(event) => {
        event.preventDefault();
        move.mutate();
      }}
    >
      <FormField label={t("New path")} disabled={move.isPending}>
        <Input
          required
          value={destination}
          autoComplete="off"
          onChange={(event) => setDestination(event.target.value)}
        />
      </FormField>
      <ErrorNotice
        error={move.error}
        retry={
          isStale(move.error)
            ? () => void invalidateMemories(cache, workspace.id)
            : undefined
        }
      />
      <FormActions
        onCancel={onCancel}
        pending={move.isPending}
        disabled={!next || next === path}
        label={t("Rename file")}
      />
    </form>
  );
}
