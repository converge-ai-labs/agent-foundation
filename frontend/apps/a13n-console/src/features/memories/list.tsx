import { BrainIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
  useCursor,
} from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Page } from "../../shared/page";
import type { Schema } from "../../shared/api";
import { useProviderTypes } from "../providers";
import { memoryQueries } from "./api";
import { CreateMemory } from "./create";
import { kindLabel, useMemoryTypeName } from "./fields";

type Kind = Schema["MemoryKind"];

export function MemoriesPage() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const [kind, setKind] = useState<Kind>();
  const [type, setType] = useState<string>();
  const typeName = useMemoryTypeName();
  const recordTypes = useProviderTypes("memory").data?.items ?? [];
  const filters = { kind, type };
  const page = useCursor(filters);
  const query = useQuery(
    memoryQueries(client, workspace.id).page(filters, page.cursor),
  );
  const create = can("write") ? (
    <CreateMemory onCreated={(memory) => navigate(memory.id)} />
  ) : undefined;
  const filtered = !!kind || !!type;
  // A type belongs to one kind, so a kind offers only its own types.
  const types = [
    ...(kind !== "record" ? ["postgres"] : []),
    ...(kind !== "file" ? recordTypes.map((item) => item.type) : []),
  ];
  return (
    <Page
      title={t("Memories")}
      description={t(
        "What agents keep across conversations: files with the history of every change, or records recalled by meaning.",
      )}
      actions={create}
      toolbar={
        <Toolbar
          filters={
            <>
              <ChoiceField
                label={t("Kind")}
                variant="filter"
                value={kind ?? "all"}
                onValueChange={(value) => {
                  const next =
                    value === "file" || value === "record" ? value : undefined;
                  setKind(next);
                  if (
                    next &&
                    type &&
                    (type === "postgres") !== (next === "file")
                  )
                    setType(undefined);
                }}
                options={[
                  { value: "all", label: t("All kinds") },
                  { value: "file", label: t("File") },
                  { value: "record", label: t("Record") },
                ]}
              />
              <ChoiceField
                label={t("Type")}
                variant="filter"
                value={type ?? "all"}
                onValueChange={(value) =>
                  setType(value === "all" ? undefined : value)
                }
                options={[
                  { value: "all", label: t("All types") },
                  ...types.map((item) => ({
                    value: item,
                    label: typeName(item),
                  })),
                ]}
              />
            </>
          }
        />
      }
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={6} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            caption={t("Memories")}
            items={query.data.items}
            onRowActivate={(memory) => navigate(memory.id)}
            columns={[
              {
                label: t("Memory"),
                tone: "primary",
                render: (memory) => (
                  <ResourceIdentity
                    to={memory.id}
                    name={memory.name}
                    description={memory.description ?? memory.key}
                    resourceId={memory.id}
                    resourceKey={memory.key}
                    icon={<BrainIcon size={16} />}
                  />
                ),
              },
              {
                label: t("Kind"),
                render: (memory) => t(kindLabel(memory.kind)),
              },
              {
                label: t("Type"),
                tone: "muted",
                render: (memory) => typeName(memory.type),
              },
              // Only the Service's own store counts a memory's files and bytes.
              {
                label: t("Files"),
                render: (memory) =>
                  memory.file_count === null
                    ? "—"
                    : t("{{count}} files", { count: memory.file_count }),
              },
              {
                label: t("Size"),
                tone: "muted",
                render: (memory) =>
                  memory.content_bytes === null || memory.history_bytes === null
                    ? "—"
                    : t("{{size}} KB", {
                        size: Math.ceil(
                          (memory.content_bytes + memory.history_bytes) / 1024,
                        ),
                      }),
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (memory) => (
                  <Timestamp value={memory.updated_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} memories on this page", {
              count: query.data.items.length,
            })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<BrainIcon size={20} />}
            title={t(filtered ? "No matching memories" : "No memories yet")}
            description={t(
              filtered
                ? "Try other filters."
                : "Create a memory to give agents what they keep across conversations.",
            )}
            action={!filtered && create}
          />
        )
      )}
    </Page>
  );
}
