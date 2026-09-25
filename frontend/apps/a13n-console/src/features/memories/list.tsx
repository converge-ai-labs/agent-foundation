import { BrainIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
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
  useCursor,
} from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Page } from "../../shared/page";
import { memoryQueries } from "./api";
import { CreateMemory } from "./create";

export function MemoriesPage() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const page = useCursor();
  const query = useQuery(memoryQueries(client, workspace.id).page(page.cursor));
  const create = can("write") ? (
    <CreateMemory onCreated={(memory) => navigate(memory.id)} />
  ) : undefined;
  return (
    <Page
      title={t("Memories")}
      description={t(
        "Files agents keep across conversations, with the history of every change.",
      )}
      actions={create}
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={4} />
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
                label: t("Files"),
                render: (memory) =>
                  t("{{count}} files", { count: memory.file_count }),
              },
              {
                label: t("Size"),
                tone: "muted",
                render: (memory) =>
                  t("{{size}} KB", {
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
            title={t("No memories yet")}
            description={t(
              "Create a memory to give agents files they keep across conversations.",
            )}
            action={create}
          />
        )
      )}
    </Page>
  );
}
