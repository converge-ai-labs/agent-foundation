import {
  ClockCounterClockwiseIcon,
  FilePlusIcon,
  FileTextIcon,
  PencilSimpleIcon,
  PushPinIcon,
} from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, StatusPill } from "a13n-ui";
import { useMemo, useState, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, ifMatch, type Schema } from "../../../shared/api";
import { Empty } from "../../../shared/collection";
import { Confirm, ConflictNotice } from "../../../shared/dialogs";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import {
  FileBody,
  FileBrowser,
  FileHeader,
  FileText,
  fileTree,
  isMarkdown,
  type FileView,
} from "../../../shared/files";
import { FormActions, TextAreaField } from "../../../shared/forms";
import { invalidateMemories, isStale, memoryQueries } from "../api";
import styles from "../memories.module.css";
import { NewFile, RenameFile } from "./file-dialogs";

type Memory = Schema["Memory"];
type Entry = Schema["MemoryFileEntry"];
/** Edited text and the file version the edit started from. */
type FileDraft = { text: string; etag?: string };

/** The first always-loaded file leads, as it leads the memory's context. */
function defaultFile(memory: Memory, files: readonly Entry[]) {
  return (
    memory.always_load.find((path) =>
      files.some((file) => file.path === path),
    ) ?? files[0]?.path
  );
}

/** The memory's files at the left and the chosen one, read or edited, at the right. */
export function MemoryFiles({ memory }: { memory: Memory }) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [search, setSearch] = useSearchParams();
  const files = useQuery(memoryQueries(client, workspace.id).files(memory.id));
  const nodes = useMemo(() => fileTree(files.data ?? []), [files.data]);
  function select(path: string | undefined) {
    setSearch(
      (current) => {
        const params = new URLSearchParams(current);
        if (path) params.set("file", path);
        else params.delete("file");
        return params;
      },
      { replace: true },
    );
  }
  if (files.isPending) return <Loading variant="detail" />;
  if (!files.data)
    return (
      <ErrorNotice error={files.error} retry={() => void files.refetch()} />
    );
  const newFile = (trigger: ReactElement) =>
    can("run") ? (
      <NewFile memory={memory} trigger={trigger} onCreated={select} />
    ) : undefined;
  if (!files.data.length)
    return (
      <Empty
        icon={<FileTextIcon size={20} />}
        title={t("No files yet")}
        description={t(
          "Agents create files as they work. You can also write the first one.",
        )}
        action={newFile(
          <Button type="button" variant="default">
            <FilePlusIcon size={14} />
            {t("New file")}
          </Button>,
        )}
      />
    );
  const requested = search.get("file");
  const entry =
    files.data.find((file) => file.path === requested) ??
    files.data.find((file) => file.path === defaultFile(memory, files.data));
  return (
    <FileBrowser
      label={t("Memory files")}
      count={files.data.length}
      nodes={nodes}
      selected={entry?.path ?? ""}
      onSelect={select}
      marker={(path) =>
        memory.always_load.includes(path) && (
          <PushPinIcon
            size={12}
            className={styles.pin}
            role="img"
            aria-label={t("Always loaded")}
          />
        )
      }
      actions={newFile(
        <Button
          type="button"
          variant="ghost"
          size="icon-xs"
          aria-label={t("New file")}
          title={t("New file")}
        >
          <FilePlusIcon />
        </Button>,
      )}
    >
      {entry && (
        <FileContent
          key={entry.path}
          memory={memory}
          entry={entry}
          onMoved={select}
          onDeleted={() => select(undefined)}
        />
      )}
    </FileBrowser>
  );
}

function FileContent({
  memory,
  entry,
  onMoved,
  onDeleted,
}: {
  memory: Memory;
  entry: Entry;
  onMoved: (path: string) => void;
  onDeleted: () => void;
}) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const file = useQuery(
    memoryQueries(client, workspace.id).file(memory.id, entry.path),
  );
  const [view, setView] = useState<FileView>("preview");
  // The edit keeps the version it started from, so a change made meanwhile
  // fails the save instead of being overwritten.
  const [draft, setDraft] = useState<FileDraft>();
  const params = { path: { memory_id: memory.id, path: entry.path } };
  const save = useMutation({
    mutationFn: (edit: FileDraft) =>
      client
        .workspace(workspace.id)
        .PUT("/api/v1/memories/{memory_id}/files/{path}", {
          params,
          headers: ifMatch(edit.etag),
          body: { content: edit.text },
        })
        .then(data),
    onSuccess: () => {
      setDraft(undefined);
      void invalidateMemories(cache, workspace.id);
    },
  });
  // Reloading either drops the draft or keeps it on the version now saved.
  const reload = useMutation({
    mutationFn: async (keep: boolean) => {
      const current = await file.refetch({ throwOnError: true });
      return { keep, etag: current.data?.etag };
    },
    onSuccess: ({ keep, etag }) => {
      save.reset();
      setDraft(keep && draft ? { text: draft.text, etag } : undefined);
    },
  });
  const pinned = memory.always_load.includes(entry.path);
  const markdown = isMarkdown(entry.path);
  const loaded = file.data;
  return (
    <>
      <FileHeader
        path={entry.path}
        size={loaded?.value.size ?? entry.size}
        marker={
          pinned && (
            <StatusPill variant="neutral">{t("Always loaded")}</StatusPill>
          )
        }
        view={markdown && !draft ? view : undefined}
        onViewChange={setView}
        actions={
          !draft && (
            <>
              <Button
                variant="ghost"
                size="icon-xs"
                render={
                  <Link
                    to={`?tab=history&path=${encodeURIComponent(entry.path)}`}
                  />
                }
                aria-label={t("History of {{path}}", { path: entry.path })}
                title={t("History")}
              >
                <ClockCounterClockwiseIcon />
              </Button>
              {can("run") && loaded && (
                <>
                  <RenameFile
                    memory={memory}
                    path={entry.path}
                    etag={loaded.etag}
                    onMoved={onMoved}
                    trigger={
                      <Button type="button" variant="ghost" size="sm">
                        {t("Rename")}
                      </Button>
                    }
                  />
                  <Confirm
                    danger
                    triggerVariant="ghost"
                    trigger={t("Delete")}
                    retry={() => void invalidateMemories(cache, workspace.id)}
                    title={t("Delete file")}
                    subject={entry.path}
                    description={t(
                      "The file's history stays, so it can be restored from History.",
                    )}
                    action={() =>
                      client
                        .workspace(workspace.id)
                        .DELETE("/api/v1/memories/{memory_id}/files/{path}", {
                          params,
                          headers: ifMatch(loaded.etag),
                        })
                    }
                    onSuccess={() => {
                      void invalidateMemories(cache, workspace.id);
                      onDeleted();
                    }}
                  />
                  <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() =>
                      setDraft({
                        text: loaded.value.content,
                        etag: loaded.etag,
                      })
                    }
                  >
                    <PencilSimpleIcon />
                    {t("Edit")}
                  </Button>
                </>
              )}
            </>
          )
        }
      />
      <FileBody>
        {draft ? (
          <form
            className={styles.editor}
            onSubmit={(event) => {
              event.preventDefault();
              save.mutate(draft);
            }}
          >
            <TextAreaField
              label={t("Content of {{path}}", { path: entry.path })}
              hideLabel
              code
              value={draft.text}
              onChange={(text) => setDraft({ ...draft, text })}
            />
            {isStale(save.error) ? (
              <ConflictNotice
                title={t("This file changed")}
                description={t(
                  "Someone or an agent changed this file after you started editing. Your text is kept here.",
                )}
                recover={{
                  label: t("Discard my text and reload"),
                  pending: reload.isPending && reload.variables === false,
                  onClick: () => reload.mutate(false),
                }}
                proceed={{
                  label: t("Keep my text"),
                  pending: reload.isPending && reload.variables === true,
                  onClick: () => reload.mutate(true),
                }}
              />
            ) : (
              <ErrorNotice error={save.error} />
            )}
            <ErrorNotice error={reload.error} />
            <FormActions
              pending={save.isPending}
              onCancel={() => {
                save.reset();
                setDraft(undefined);
              }}
              disabled={isStale(save.error)}
              label={t("Save file")}
            />
          </form>
        ) : file.isPending ? (
          <Loading variant="code" />
        ) : !loaded ? (
          <ErrorNotice error={file.error} retry={() => void file.refetch()} />
        ) : (
          <FileText
            text={loaded.value.content}
            preview={markdown && view === "preview"}
          />
        )}
      </FileBody>
    </>
  );
}
