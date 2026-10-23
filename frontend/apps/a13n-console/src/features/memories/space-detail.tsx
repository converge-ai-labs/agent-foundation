import { BrainIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import { IconTile } from "../../shared/identity";
import { DetailHeader, DetailPage, useTabParam } from "../../shared/page";
import { memoryQueries } from "./api";
import { MemoryFiles } from "./detail/files";
import { MemoryHistory } from "./detail/history";
import { SpaceConfiguration, type SpaceResource } from "./space-editor";
import { SpaceOrganization, SpaceSessions } from "./space-organization";
import { spaceQueries } from "./spaces-api";

const tabs = [
  "files",
  "history",
  "organization",
  "sessions",
  "configuration",
] as const;
export function MemorySpaceDetail() {
  const { spaceId = "" } = useParams(),
    client = useClient(),
    { workspace } = useWorkspace();
  const query = useQuery(spaceQueries(client, workspace.id).detail(spaceId));
  if (query.isPending) return <Loading variant="detail" page />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return <SpaceTabs resource={query.data} />;
}
function SpaceTabs({ resource }: { resource: SpaceResource }) {
  const { t } = useTranslation(),
    [tab, setTab] = useTabParam(tabs),
    client = useClient(),
    { workspace } = useWorkspace();
  const space = resource.value;
  const backing = useQuery(
    memoryQueries(client, workspace.id).memory(space.memory_id),
  );
  const labels = {
    files: t("Files"),
    history: t("File history"),
    organization: t("Organization"),
    sessions: t("Sessions"),
    configuration: t("Configuration"),
  };
  return (
    <DetailPage
      back="../memory-spaces"
      backLabel={t("Memory spaces")}
      tabs={tabs.map((value) => ({
        value,
        label: labels[value],
        ...(value === "files" && {
          count: backing.data?.value.file_count ?? space.file_count,
        }),
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
          name={space.name}
          status={<StatePill state={space.enabled ? "enabled" : "disabled"} />}
          description={space.description}
        />
      }
    >
      {tab === "configuration" ? (
        <SpaceConfiguration resource={resource} />
      ) : tab === "organization" ? (
        <SpaceOrganization space={space} />
      ) : tab === "sessions" ? (
        <SpaceSessions spaceId={space.id} />
      ) : (
        <>
          <ErrorNotice
            error={backing.error}
            retry={() => void backing.refetch()}
          />
          {backing.data ? (
            tab === "files" ? (
              <MemoryFiles memory={backing.data.value} />
            ) : (
              <MemoryHistory memory={backing.data.value} />
            )
          ) : (
            backing.isPending && <Loading variant="detail" />
          )}
        </>
      )}
    </DetailPage>
  );
}
