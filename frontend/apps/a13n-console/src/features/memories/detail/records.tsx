import {
  NotePencilIcon,
  PencilSimpleIcon,
  PlusIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import {
  useMutation,
  useQuery,
  useQueryClient,
  type QueryClient,
} from "@tanstack/react-query";
import { Button, MenuItem, ModalFrame } from "a13n-ui";
import { useState, type ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, type Schema } from "../../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../../shared/collection";
import {
  Confirm,
  useResourceEditorState,
  useResourceRows,
  type ResourceEditorControl,
} from "../../../shared/dialogs";
import { ErrorNotice, Loading, Timestamp } from "../../../shared/feedback";
import { FormActions, TextAreaField } from "../../../shared/forms";
import shared from "../../../shared/shared.module.css";
import { isUnconfirmed, memoryKeys, memoryQueries } from "../api";
import styles from "../memories.module.css";

type Memory = Schema["Memory"];
type MemoryRecord = Schema["MemoryRecordView"];

/**
 * The contract's bound on one record. A deployment's `memory.record_chars` may
 * be lower; the Service refuses a longer text when it is saved.
 */
const RECORD_CHARS = 8000;

/** A record's length as the Service counts it: in characters, not UTF-16 units. */
function recordLength(text: string) {
  return [...text].length;
}

function refreshRecords(cache: QueryClient, workspaceId: string, id: string) {
  return cache.invalidateQueries({
    queryKey: memoryKeys(workspaceId).records(id),
  });
}

/**
 * A record memory's records, in its provider's order or, for a search, closest
 * in meaning first. Records carry no version: an edit replaces the text,
 * whoever wrote it last.
 */
export function MemoryRecords({ memory }: { memory: Memory }) {
  const client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation();
  const queries = memoryQueries(client, workspace.id);
  const [text, setText] = useState("");
  // The query searched for; empty lists the records instead.
  const [search, setSearch] = useState("");
  const page = useCursor(search);
  const records = useQuery(queries.records(memory.id, search, page.cursor));
  // The editor lives outside the row menu, which would take its keystrokes.
  const editor = useResourceRows<MemoryRecord>();
  const add = can("run") ? (
    <RecordDialog
      memory={memory}
      trigger={
        <Button type="button" variant="default">
          <PlusIcon size={14} />
          {t("Add record")}
        </Button>
      }
    />
  ) : undefined;
  return (
    <div className="grid gap-4">
      <form
        role="search"
        onSubmit={(event) => {
          event.preventDefault();
          setSearch(text.trim());
        }}
      >
        <Toolbar
          search={text}
          searchLabel={t("Search records")}
          searchPlaceholder={t("Search by meaning, then press Enter")}
          onSearchChange={(value) => {
            setText(value);
            if (!value.trim()) setSearch("");
          }}
          trailing={add}
        />
      </form>
      {records.isPending ? (
        <Loading variant="table" columns={3} />
      ) : !records.data ? (
        <ErrorNotice
          error={records.error}
          retry={() => void records.refetch()}
        />
      ) : records.data.items.length ? (
        <>
          <ResourceTable
            caption={t("Records")}
            items={records.data.items}
            rowMenuLabel={t("Record actions")}
            rowMenu={
              can("run")
                ? (item) => (
                    <RecordActions
                      memory={memory}
                      record={item}
                      onEdit={(element) => editor.activate(item, element)}
                    />
                  )
                : undefined
            }
            columns={[
              {
                label: t("Record"),
                tone: "primary",
                render: (item) => (
                  <span className={styles.recordText}>{item.text}</span>
                ),
              },
              ...(search
                ? [
                    {
                      label: t("Score"),
                      render: (item: MemoryRecord) =>
                        item.score === null || item.score === undefined
                          ? "—"
                          : item.score.toFixed(2),
                    },
                  ]
                : []),
              {
                label: t("Updated"),
                tone: "muted",
                render: (item) =>
                  item.updated_at ? (
                    <Timestamp value={item.updated_at} relative />
                  ) : (
                    "—"
                  ),
              },
            ]}
          />
          <CollectionFooter
            count={t(
              search
                ? "{{count}} closest records"
                : "{{count}} records on this page",
              { count: records.data.items.length },
            )}
          >
            {!search && (
              <Pagination page={page} next={records.data.next_cursor} />
            )}
          </CollectionFooter>
        </>
      ) : search ? (
        <Empty
          icon={<NotePencilIcon size={20} />}
          title={t("No matching records")}
          description={t("Try other words.")}
        />
      ) : (
        <Empty
          icon={<NotePencilIcon size={20} />}
          title={t("No records yet")}
          description={t(
            "Agents add records as they work. You can also add the first one.",
          )}
          action={add}
        />
      )}
      {editor.selected && (
        <RecordDialog
          key={editor.selected.id}
          memory={memory}
          record={editor.selected}
          {...editor.control}
        />
      )}
    </div>
  );
}

function RecordActions({
  memory,
  record,
  onEdit,
}: {
  memory: Memory;
  record: MemoryRecord;
  onEdit: (element: HTMLElement) => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const refresh = () => void refreshRecords(cache, workspace.id, memory.id);
  return (
    <>
      <MenuItem onClick={(event) => onEdit(event.currentTarget)}>
        <PencilSimpleIcon size={14} aria-hidden="true" />
        {t("Edit")}
      </MenuItem>
      <Confirm
        danger
        title={t("Delete record")}
        subject={record.text}
        description={t(
          "The record is deleted from the memory's provider. This cannot be undone.",
        )}
        retry={refresh}
        action={() =>
          client.http.DELETE(
            "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/records/{record_id}",
            {
              params: {
                path: {
                  workspace_id: workspace.id,
                  memory_id: memory.id,
                  record_id: record.id,
                },
              },
            },
          )
        }
        onSuccess={refresh}
        triggerElement={
          <MenuItem closeOnClick={false} variant="destructive">
            <TrashIcon size={14} aria-hidden="true" />
            {t("Delete")}
          </MenuItem>
        }
      />
    </>
  );
}

/** Adds a record, or replaces the whole text of `record`. */
function RecordDialog({
  memory,
  record,
  trigger,
  ...control
}: {
  memory: Memory;
  record?: MemoryRecord;
  trigger?: ReactElement;
} & ResourceEditorControl) {
  const { t } = useTranslation();
  const { open, setOpen, modalProps } = useResourceEditorState(control);
  return (
    <ModalFrame
      {...modalProps}
      trigger={trigger}
      size="lg"
      title={record ? t("Edit record") : t("Add record")}
      description={t(
        record
          ? "The new text replaces the record's text, whoever changed it last."
          : "Agents find the record by meaning when a run recalls or searches this memory.",
      )}
      closeLabel={t("Close")}
    >
      {open && (
        <RecordForm
          memory={memory}
          record={record}
          onDone={() => setOpen(false)}
        />
      )}
    </ModalFrame>
  );
}

function RecordForm({
  memory,
  record,
  onDone,
}: {
  memory: Memory;
  record?: MemoryRecord;
  onDone: () => void;
}) {
  const client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const [text, setText] = useState(record?.text ?? "");
  const path = { workspace_id: workspace.id, memory_id: memory.id };
  const save = useMutation({
    mutationFn: () =>
      (record
        ? client.http.PUT(
            "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/records/{record_id}",
            {
              params: { path: { ...path, record_id: record.id } },
              body: { text },
            },
          )
        : client.http.POST(
            "/api/v1/workspaces/{workspace_id}/memories/{memory_id}/records",
            { params: { path }, body: { text } },
          )
      ).then(data),
    onSuccess: () => {
      void refreshRecords(cache, workspace.id, memory.id);
      onDone();
    },
  });
  const length = recordLength(text);
  return (
    <form
      className={shared.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate();
      }}
    >
      <TextAreaField
        label={t("Text")}
        rows={6}
        required
        value={text}
        onChange={setText}
        hint={t("{{count}} of {{limit}} characters", {
          count: length,
          limit: RECORD_CHARS,
        })}
        error={
          length > RECORD_CHARS
            ? t("A record holds at most {{limit}} characters.", {
                limit: RECORD_CHARS,
              })
            : undefined
        }
      />
      {/* An unconfirmed write may have happened: the list shows whether it did. */}
      <ErrorNotice
        error={save.error}
        retry={
          isUnconfirmed(save.error)
            ? () => {
                void refreshRecords(cache, workspace.id, memory.id);
                onDone();
              }
            : undefined
        }
      />
      <FormActions
        onCancel={onDone}
        pending={save.isPending}
        disabled={
          !text.trim() ||
          length > RECORD_CHARS ||
          text === record?.text ||
          isUnconfirmed(save.error)
        }
        label={record ? t("Save record") : t("Add record")}
      />
    </form>
  );
}
