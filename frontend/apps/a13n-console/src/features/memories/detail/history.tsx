import {
  ArrowRightIcon,
  CaretDownIcon,
  CaretRightIcon,
  FilePlusIcon,
  PencilSimpleIcon,
  TrashIcon,
  XIcon,
} from "@phosphor-icons/react";
import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField } from "a13n-ui";
import { useState } from "react";
import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useAuth, useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../../shared/api";
import {
  CollectionFooter,
  ListRow,
  ListRows,
  ListRowsEmpty,
  Pagination,
  useCursor,
} from "../../../shared/collection";
import { Confirm } from "../../../shared/dialogs";
import { Patch } from "../../../shared/diff";
import { ErrorNotice, Loading, Timestamp } from "../../../shared/feedback";
import { MemoryRunLink as RunLink } from "../run-link";
import { invalidateMemories, memoryQueries } from "../api";
import styles from "../memories.module.css";

type Memory = Schema["Memory"];
type Revision = Schema["MemoryRevision"];

/** Paths never start with a slash, so it stands for every file. */
const ALL_FILES = "/";

const OP_ICONS = {
  create: FilePlusIcon,
  update: PencilSimpleIcon,
  delete: TrashIcon,
  move_out: ArrowRightIcon,
  move_in: ArrowRightIcon,
} as const;

function opLabel(revision: Revision, t: TFunction) {
  const other = revision.moved_path ?? "";
  switch (revision.op) {
    case "create":
      return t("File created");
    case "update":
      return t("File edited");
    case "delete":
      return t("File deleted");
    case "move_out":
      return t("Moved to {{path}}", { path: other });
    case "move_in":
      return t("Moved from {{path}}", { path: other });
  }
}

/**
 * Every retained change, newest first, filtered by file and by run through
 * the URL so a run can link to what it changed. A change opens its diff and
 * can be undone; a file's history can be purged by people who may configure
 * the memory.
 */
export function MemoryHistory({ memory }: { memory: Memory }) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    [search, setSearch] = useSearchParams(),
    me = useAuth().data?.user.value;
  const path = search.get("path") ?? undefined,
    run = search.get("run") ?? undefined;
  const queries = memoryQueries(client, workspace.id);
  const page = useCursor({ path, run });
  const files = useQuery(queries.files(memory.id));
  const revisions = useQuery(
    queries.revisions(memory.id, { path, run }, page.cursor),
  );
  const [expanded, setExpanded] = useState<number>();
  function filter(name: "path" | "run", value: string | undefined) {
    setSearch(
      (current) => {
        const params = new URLSearchParams(current);
        if (value) params.set(name, value);
        else params.delete(name);
        return params;
      },
      { replace: true },
    );
  }
  const paths = [
    ...new Set([
      ...(files.data ?? []).map((file) => file.path),
      ...(path ? [path] : []),
    ]),
  ].sort();
  const current = (filePath: string) =>
    files.data?.find((file) => file.path === filePath);
  return (
    <div>
      <div className={styles.historyFilters}>
        <ChoiceField
          label={t("File")}
          variant="filter"
          value={path ?? ALL_FILES}
          onValueChange={(value) =>
            filter("path", value === ALL_FILES ? undefined : value)
          }
          options={[
            { value: ALL_FILES, label: t("All files") },
            ...paths.map((item) => ({ value: item, label: item })),
          ]}
        />
        {run && (
          <span className={styles.runFilter}>
            {t("Run")} <RunLink runId={run} />
            <Button
              type="button"
              variant="ghost"
              size="icon-xs"
              aria-label={t("Show changes by every run")}
              onClick={() => filter("run", undefined)}
            >
              <XIcon />
            </Button>
          </span>
        )}
        {path && can("write") && (
          <span className={styles.historyActions}>
            <Confirm
              danger
              triggerVariant="ghost"
              trigger={t("Delete history")}
              title={t("Delete file history")}
              subject={path}
              description={t(
                "Every retained change of this path is deleted, and the file itself stays. Content that runs already read stays in their conversation histories and traces.",
              )}
              action={() =>
                client
                  .workspace(workspace.id)
                  .DELETE("/api/v1/memories/{memory_id}/revisions", {
                    params: {
                      path: {
                        memory_id: memory.id,
                      },
                      query: { path },
                    },
                  })
                  .then(data)
              }
              onSuccess={() => void invalidateMemories(cache, workspace.id)}
            />
          </span>
        )}
      </div>
      <ErrorNotice error={files.error} />
      {revisions.isPending ? (
        <Loading variant="list" rows={4} />
      ) : !revisions.data ? (
        <ErrorNotice
          error={revisions.error}
          retry={() => void revisions.refetch()}
        />
      ) : !revisions.data.items.length ? (
        <ListRowsEmpty>
          {path || run
            ? t("No retained changes match these filters.")
            : t("No changes yet.")}
        </ListRowsEmpty>
      ) : (
        <>
          <ListRows>
            {revisions.data.items.map((revision) => {
              const Icon = OP_ICONS[revision.op];
              const open = expanded === revision.seq;
              const file = current(revision.path);
              return (
                <div className={styles.revisionGroup} key={revision.seq}>
                  <ListRow
                    className={styles.revisionRow}
                    icon={<Icon size={15} />}
                    name={
                      <>
                        <span className={styles.revision}>#{revision.seq}</span>{" "}
                        {revision.path}
                      </>
                    }
                    secondary={
                      <>
                        {opLabel(revision, t)}
                        {" · "}
                        <Timestamp value={revision.created_at} relative />
                        {" · "}
                        {revision.run_id ? (
                          <RunLink runId={revision.run_id} />
                        ) : !revision.principal_id ? (
                          t("Unknown")
                        ) : revision.principal_id === me?.id ? (
                          me.name
                        ) : (
                          // Only administrators may list members, so others keep a short reference.
                          <span title={revision.principal_id}>
                            {t("Member · {{reference}}", {
                              reference: revision.principal_id.slice(-8),
                            })}
                          </span>
                        )}
                      </>
                    }
                    actions={
                      <span className={styles.revisionActions}>
                        <Button
                          type="button"
                          variant="ghost"
                          size="sm"
                          aria-expanded={open}
                          onClick={() =>
                            setExpanded(open ? undefined : revision.seq)
                          }
                        >
                          {open ? <CaretDownIcon /> : <CaretRightIcon />}
                          {t("Changes")}
                        </Button>
                        {can("run") && (
                          <Confirm
                            triggerElement={
                              <Button type="button" variant="ghost" size="sm">
                                {t("Restore")}
                              </Button>
                            }
                            // A newer file at the path needs a fresh read first.
                            retry={() =>
                              void invalidateMemories(cache, workspace.id)
                            }
                            title={t("Restore file")}
                            subject={`${revision.path} · #${revision.seq}`}
                            description={
                              revision.op === "create" ||
                              revision.op === "move_in"
                                ? t(
                                    "The path held no file before this change, so restoring deletes the file there. The restore is recorded as a new change.",
                                  )
                                : t(
                                    "The path returns to its content before this change. The restore is recorded as a new change.",
                                  )
                            }
                            action={() =>
                              client
                                .workspace(workspace.id)
                                .POST(
                                  "/api/v1/memories/{memory_id}/revisions/{seq}/restore",
                                  {
                                    params: {
                                      path: {
                                        memory_id: memory.id,
                                        seq: revision.seq,
                                      },
                                    },
                                    // The file now at the path, as the reader saw it.
                                    headers: ifMatch(
                                      file ? rowTag(file) : undefined,
                                    ),
                                  },
                                )
                                .then(data)
                            }
                            onSuccess={() =>
                              void invalidateMemories(cache, workspace.id)
                            }
                          />
                        )}
                      </span>
                    }
                  />
                  {open && <RevisionDiff memory={memory} seq={revision.seq} />}
                </div>
              );
            })}
          </ListRows>
          <CollectionFooter>
            <Pagination page={page} next={revisions.data.next_cursor} />
          </CollectionFooter>
        </>
      )}
    </div>
  );
}

function RevisionDiff({ memory, seq }: { memory: Memory; seq: number }) {
  const client = useClient(),
    { workspace } = useWorkspace();
  const detail = useQuery(
    memoryQueries(client, workspace.id).revision(memory.id, seq),
  );
  return (
    <div className={styles.revisionDiff}>
      {detail.isPending ? (
        <Loading variant="code" />
      ) : !detail.data ? (
        <ErrorNotice error={detail.error} retry={() => void detail.refetch()} />
      ) : (
        <Patch hunks={detail.data.hunks} />
      )}
    </div>
  );
}
