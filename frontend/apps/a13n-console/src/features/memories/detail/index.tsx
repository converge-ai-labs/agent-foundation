import { BrainIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import { IconTile } from "../../../shared/identity";
import { DetailHeader, DetailPage, useTabParam } from "../../../shared/page";
import { memoryQueries } from "../api";
import { MemoryConfiguration } from "./configuration";
import { MemoryFiles } from "./files";
import { MemoryHistory } from "./history";

export function MemoryDetail() {
  const { memoryId = "" } = useParams(),
    client = useClient(),
    { workspace } = useWorkspace(),
    { t } = useTranslation();
  const [tab, setTab] = useTabParam(["files", "history", "configuration"]);
  const query = useQuery(memoryQueries(client, workspace.id).memory(memoryId));
  if (query.isPending) return <Loading variant="detail" page />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const memory = query.data.value;
  return (
    <DetailPage
      back="../memories"
      backLabel={t("Memories")}
      tabs={[
        { value: "files", label: t("Files"), count: memory.file_count },
        { value: "history", label: t("History") },
        { value: "configuration", label: t("Configuration") },
      ]}
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
          resourceKey={memory.key}
          description={memory.description}
        />
      }
    >
      {tab === "files" ? (
        <MemoryFiles memory={memory} />
      ) : tab === "history" ? (
        <MemoryHistory memory={memory} />
      ) : (
        <MemoryConfiguration resource={query.data} />
      )}
    </DetailPage>
  );
}
