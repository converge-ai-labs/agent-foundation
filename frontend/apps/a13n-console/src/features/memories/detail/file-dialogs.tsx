import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ModalFrame } from "a13n-ui";
import { useState, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, ifMatch, type Schema } from "../../../shared/api";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { FormActions, TextAreaField } from "../../../shared/forms";
import shared from "../../../shared/shared.module.css";
import { invalidateMemories, isStale, memoryQueries } from "../api";

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
      client
        .workspace(workspace.id)
        .POST("/api/v1/memories/{memory_id}/files", {
          params: {
            path: { memory_id: memory.id },
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

export function FileActionDialog({
  memory,
  path,
  kind,
  onClose,
  onMoved,
  onDeleted,
}: {
  memory: Memory;
  path: string;
  kind: "rename" | "delete";
  onClose: () => void;
  onMoved: (path: string) => void;
  onDeleted: () => void;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace();
  const file = useQuery(
    memoryQueries(client, workspace.id).file(memory.id, path),
  );
  return (
    <ModalFrame
      open
      onOpenChange={(open) => {
        if (!open) onClose();
      }}
      title={t(kind === "rename" ? "Rename file" : "Delete file")}
      description={t(
        kind === "rename"
          ? "The file keeps its history. Always-loaded paths are not updated."
          : "The file's history stays, so it can be restored from History.",
      )}
      closeLabel={t("Close")}
    >
      {file.data ? (
        kind === "rename" ? (
          <RenameForm
            memory={memory}
            path={path}
            etag={file.data.etag}
            onCancel={onClose}
            onMoved={(destination) => {
              onClose();
              onMoved(destination);
            }}
          />
        ) : (
          <DeleteFileForm
            memory={memory}
            path={path}
            etag={file.data.etag}
            onCancel={onClose}
            onDeleted={() => {
              onClose();
              onDeleted();
            }}
          />
        )
      ) : file.isPending ? (
        <Loading variant="code" />
      ) : (
        <ErrorNotice error={file.error} retry={() => void file.refetch()} />
      )}
    </ModalFrame>
  );
}

function DeleteFileForm({
  memory,
  path,
  etag,
  onCancel,
  onDeleted,
}: {
  memory: Memory;
  path: string;
  etag?: string;
  onCancel: () => void;
  onDeleted: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const [version] = useState(etag);
  const remove = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .DELETE("/api/v1/memories/{memory_id}/files/{path}", {
          params: { path: { memory_id: memory.id, path } },
          headers: ifMatch(version),
        }),
    onSuccess: () => {
      void invalidateMemories(cache, workspace.id);
      onDeleted();
    },
  });
  return (
    <div className={shared.form}>
      <p className="break-all font-medium">{path}</p>
      <ErrorNotice error={remove.error} />
      <div className="flex justify-end gap-2">
        <Button
          variant="outline"
          disabled={remove.isPending}
          onClick={onCancel}
        >
          {t("Cancel")}
        </Button>
        <Button
          variant="destructive"
          loading={remove.isPending}
          onClick={() => remove.mutate()}
        >
          {t("Delete file")}
        </Button>
      </div>
    </div>
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
  const [version] = useState(etag);
  const move = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/memories/{memory_id}/files/move", {
          params: {
            path: { memory_id: memory.id },
          },
          headers: ifMatch(version),
          body: { source: path, destination: destination.trim() },
        })
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
