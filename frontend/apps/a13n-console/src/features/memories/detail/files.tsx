import {
  ClockCounterClockwiseIcon,
  DotsThreeOutlineVerticalIcon,
  TextAaIcon,
  TrashIcon,
  FilePlusIcon,
  FileTextIcon,
  PencilSimpleIcon,
  PushPinIcon,
} from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  Menu,
  MenuItem,
  MenuLinkItem,
  MenuPopup,
  MenuSeparator,
  MenuTrigger,
  StatusPill,
} from "a13n-ui";
import {
  useEffect,
  useMemo,
  useState,
  type ReactElement,
  type ReactNode,
} from "react";
import { useTranslation } from "react-i18next";
import { Link, useSearchParams } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, ifMatch, type Schema } from "../../../shared/api";
import { Empty } from "../../../shared/collection";
import { ConflictNotice } from "../../../shared/dialogs";
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
import { FileActionDialog, NewFile } from "./file-dialogs";

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
  const [action, setAction] = useState<{
    path: string;
    kind: "edit" | "rename" | "delete";
  }>();
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
  const fileMenu = (path: string) => (
    <MemoryFileActions
      path={path}
      onAction={(kind) => {
        if (kind === "edit") select(path);
        setAction({ path, kind });
      }}
    />
  );
  return (
    <>
      <FileBrowser
        embedded
        label={t("Memory files")}
        fileMenu={fileMenu}
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
          <Button type="button" variant="ghost" size="sm">
            <FilePlusIcon />
            {t("New file")}
          </Button>,
        )}
      >
        {entry && (
          <FileContent
            key={entry.path}
            memory={memory}
            entry={entry}
            menu={fileMenu(entry.path)}
            editRequested={
              action?.kind === "edit" && action.path === entry.path
            }
            onEditStarted={() => setAction(undefined)}
          />
        )}
      </FileBrowser>
      {action && action.kind !== "edit" && (
        <FileActionDialog
          memory={memory}
          path={action.path}
          kind={action.kind}
          onClose={() => setAction(undefined)}
          onMoved={select}
          onDeleted={() => {
            if (entry?.path === action.path) select(undefined);
          }}
        />
      )}
    </>
  );
}

function MemoryFileActions({
  path,
  onAction,
}: {
  path: string;
  onAction: (kind: "edit" | "rename" | "delete") => void;
}) {
  const { t } = useTranslation(),
    { can } = useWorkspace();
  return (
    <>
      {can("run") && (
        <MenuItem onClick={() => onAction("edit")}>
          <PencilSimpleIcon />
          {t("Edit")}
        </MenuItem>
      )}
      <MenuLinkItem
        render={<Link to={`?tab=history&path=${encodeURIComponent(path)}`} />}
      >
        <ClockCounterClockwiseIcon />
        {t("History")}
      </MenuLinkItem>
      {can("run") && (
        <>
          <MenuItem onClick={() => onAction("rename")}>
            <TextAaIcon />
            {t("Rename")}
          </MenuItem>
          <MenuSeparator />
          <MenuItem variant="destructive" onClick={() => onAction("delete")}>
            <TrashIcon />
            {t("Delete")}
          </MenuItem>
        </>
      )}
    </>
  );
}

function FileContent({
  memory,
  entry,
  menu,
  editRequested,
  onEditStarted,
}: {
  memory: Memory;
  entry: Entry;
  menu: ReactNode;
  editRequested: boolean;
  onEditStarted: () => void;
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
  useEffect(() => {
    if (editRequested && file.data) {
      setDraft({ text: file.data.value.content, etag: file.data.etag });
      onEditStarted();
    }
  }, [editRequested, file.data, onEditStarted]);
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
                  <Button
                    type="button"
                    variant="ghost"
                    size="icon-xs"
                    aria-label={t("Edit")}
                    title={t("Edit")}
                    onClick={() =>
                      setDraft({
                        text: loaded.value.content,
                        etag: loaded.etag,
                      })
                    }
                  >
                    <PencilSimpleIcon />
                  </Button>
                </>
              )}
              <Menu>
                <MenuTrigger
                  render={
                    <Button
                      variant="ghost"
                      size="icon-xs"
                      aria-label={t("File actions")}
                      title={t("File actions")}
                    />
                  }
                >
                  <DotsThreeOutlineVerticalIcon size={14} weight="fill" />
                </MenuTrigger>
                <MenuPopup align="end">{menu}</MenuPopup>
              </Menu>
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
