import { BrainIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Schema } from "../../../shared/api";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { IconTile } from "../../../shared/identity";
import { DetailHeader, DetailPage, useTabParam } from "../../../shared/page";
import { memoryQueries } from "../api";
import { MemoryConfiguration } from "./configuration";
import { MemoryFiles } from "./files";
import { MemoryHistory } from "./history";
import { MemoryRecords } from "./records";

type Resource = { value: Schema["Memory"]; etag?: string };

/** A file memory's files and their history, or a record memory's records. */
const tabs = {
  file: ["files", "history", "configuration"],
  record: ["records", "configuration"],
} as const;

export function MemoryDetail() {
  const { memoryId = "" } = useParams(),
    client = useClient(),
    { workspace } = useWorkspace();
  const query = useQuery(memoryQueries(client, workspace.id).memory(memoryId));
  if (query.isPending) return <Loading variant="detail" page />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return <MemoryTabs resource={query.data} />;
}

function MemoryTabs({ resource }: { resource: Resource }) {
  const { t } = useTranslation();
  const memory = resource.value;
  const [tab, setTab] = useTabParam(tabs[memory.kind]);
  const labels = {
    files: t("Files"),
    history: t("History"),
    records: t("Records"),
    configuration: t("Configuration"),
  };
  return (
    <DetailPage
      back="../memories"
      backLabel={t("Memories")}
      tabs={tabs[memory.kind].map((value) => ({
        value,
        label: labels[value],
        ...(value === "files" && { count: memory.file_count ?? undefined }),
      }))}
      tab={tab}
      onTabChange={setTab}
      header={
        <DetailHeader
          avatar={
            <IconTile size={44}>
              <BrainIcon size={20} />
            </IconTile>
          }
          name={memory.name}
          description={memory.description}
        />
      }
    >
      {tab === "files" ? (
        <MemoryFiles memory={memory} />
      ) : tab === "history" ? (
        <MemoryHistory memory={memory} />
      ) : tab === "records" ? (
        <MemoryRecords memory={memory} />
      ) : (
        <MemoryConfiguration resource={resource} />
      )}
    </DetailPage>
  );
}
