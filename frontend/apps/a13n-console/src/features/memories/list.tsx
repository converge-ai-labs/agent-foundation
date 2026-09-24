import { BrainIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
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
import { memoryQueries } from "./api";
import { CreateMemory } from "./create";
import { parseLabels } from "./form";
import { LabelChips } from "./fields";

/** The label selectors a filter names, or an error for a malformed one. */
function labelSelectors(text: string) {
  try {
    return {
      selectors: Object.entries(parseLabels(text)).map(
        ([key, value]) => `${key}:${value}`,
      ),
    };
  } catch (error) {
    return { selectors: [], error: error as Error };
  }
}

export function MemoriesPage() {
  const { workspace, can } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const [filter, setFilter] = useState("");
  const { selectors, error: filterError } = labelSelectors(filter);
  const page = useCursor(selectors);
  const query = useQuery({
    ...memoryQueries(client, workspace.id).page(selectors, page.cursor),
    enabled: !filterError,
  });
  const create = can("write") ? (
    <CreateMemory onCreated={(memory) => navigate(memory.id)} />
  ) : undefined;
  const filtered = selectors.length > 0;
  return (
    <Page
      title={t("Memories")}
      description={t(
        "Files agents keep across conversations, with the history of every change.",
      )}
      actions={create}
      toolbar={
        <Toolbar
          search={filter}
          searchLabel={t("Filter by label")}
          searchPlaceholder={t("Filter by label, such as team:docs")}
          onSearchChange={setFilter}
        />
      }
    >
      {filterError ? (
        <p role="alert" className="text-sm text-destructive-foreground">
          {t(filterError.message)}
        </p>
      ) : (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      )}
      {filterError ? null : query.isPending ? (
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
                label: t("Labels"),
                tone: "muted",
                render: (memory) => <LabelChips labels={memory.labels} />,
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
                ? "Try other labels."
                : "Create a memory to give agents files they keep across conversations.",
            )}
            action={!filtered && create}
          />
        )
      )}
    </Page>
  );
}
