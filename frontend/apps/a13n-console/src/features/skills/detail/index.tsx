import { StatusPill } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { representation } from "../../../shared/api";
import { ErrorNotice, Loading, Timestamp } from "../../../shared/feedback";
import { IconTile } from "../../../shared/identity";
import { DetailHeader, DetailPage, useTabParam } from "../../../shared/page";
import { ImportSkill } from "../import-dialog";
import { revisionQuery, revisionsQuery } from "../revisions";
import { SkillIcon, skillSource } from "../source";
import styles from "../skills.module.css";
import { SkillMenu } from "./actions";
import { SkillFiles } from "./files";
import { UsedByAgents } from "./used-by";
import { Revisions } from "./versions";

export function SkillDetail() {
  const { skillId = "" } = useParams(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [search] = useSearchParams();
  const [tab, setTab] = useTabParam(["files", "versions", "references"]);
  const query = useQuery({
    queryKey: ["skills", workspace.id, skillId],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/skills/{skill_id}", {
          params: { path: { skill_id: skillId } },
          signal,
        })
        .then(representation),
  });
  const skill = query.data?.value;
  const revisionId = search.get("revision") ?? skill?.default_revision_id ?? "";
  // One fetch serves the header summary and the file browser, so opening a
  // retained version never asks for the same manifest twice.
  const revision = useQuery({
    ...revisionQuery(client, {
      id: revisionId,
      skill_id: skillId,
      workspace_id: workspace.id,
    }),
    enabled: !!skill && !!revisionId,
  });
  // The first page of the Versions tab leads with the latest version.
  const latest = useQuery({
    ...revisionsQuery(client, workspace.id, skillId),
    enabled: !!skill,
  }).data?.items[0]?.number;
  if (query.isPending) return <Loading variant="detail" page />;
  if (!query.data || !skill)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const current = revision.data?.id === skill.default_revision_id;
  return (
    <DetailPage
      back="../skills"
      backLabel={t("Skills")}
      tabs={[
        { value: "files", label: t("Files") },
        {
          value: "versions",
          label: t("Versions"),
          count: latest === undefined ? undefined : `v${latest}`,
        },
        { value: "references", label: t("Used by") },
      ]}
      tab={tab}
      onTabChange={setTab}
      header={
        <DetailHeader
          avatar={
            <IconTile size={44}>
              <SkillIcon size={20} />
            </IconTile>
          }
          name={skill.name}
          status={
            skill.archived_at ? (
              <StatusPill variant="neutral">{t("Archived")}</StatusPill>
            ) : undefined
          }
          description={revision.data?.config.description}
          actions={
            <>
              {can("write") && !skill.archived_at && (
                <ImportSkill skill={skill} />
              )}
              <SkillMenu
                resource={query.data}
                revisionId={revisionId}
                revision={revision.data}
              />
            </>
          }
        />
      }
    >
      {tab === "files" ? (
        revision.isPending ? (
          <Loading variant="detail" />
        ) : !revision.data ? (
          <ErrorNotice
            error={revision.error}
            retry={() => void revision.refetch()}
          />
        ) : (
          <>
            {!current && (
              <p className={styles.retained}>
                <StatusPill variant="warning">
                  {t("Retained version")}
                </StatusPill>
                <span>v{revision.data.number}</span>
                <Timestamp value={revision.data.created_at} relative />
                <span>
                  {skillSource(revision.data.config.source.kind) === "github"
                    ? "GitHub"
                    : t("ZIP")}
                </span>
                <Link to="?tab=files">{t("View default version")}</Link>
              </p>
            )}
            <SkillFiles key={revision.data.id} revision={revision.data} />
          </>
        )
      ) : tab === "versions" ? (
        <Revisions skill={skill} etag={query.data.etag} />
      ) : (
        <UsedByAgents skill={skill} />
      )}
    </DetailPage>
  );
}
