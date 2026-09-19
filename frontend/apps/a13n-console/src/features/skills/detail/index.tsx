import { StatusPill } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link, useParams, useSearchParams } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, representation, workspaceHeaders } from "../../../shared/api";
import { ErrorNotice, Loading, Timestamp } from "../../../shared/feedback";
import { IconTile } from "../../../shared/identity";
import { DetailHeader, DetailPage, useTabParam } from "../../../shared/page";
import { ImportSkill } from "../import-dialog";
import { SkillIcon, skillSource } from "../source";
import styles from "../skills.module.css";
import { SkillMenu } from "./actions";
import { SkillFiles } from "./files";
import { UsedByAgents } from "./used-by";
import { Revisions } from "./versions";

export function SkillDetail() {
  const { skillKey = "" } = useParams(),
    client = useClient(),
    { workspace, can } = useWorkspace(),
    { t } = useTranslation(),
    [search] = useSearchParams();
  const [tab, setTab] = useTabParam(["files", "versions", "references"]);
  const query = useQuery({
    queryKey: ["skills", workspace.id, "key", skillKey],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/skills/{skill_key}", {
          params: { path: { workspace: workspace.id, skill_key: skillKey } },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(representation),
  });
  const skill = query.data?.value;
  const revisionId = search.get("revision") ?? skill?.default_revision_id ?? "";
  // One fetch serves the header summary and the file browser, so opening a
  // retained version never asks for the same manifest twice.
  const revision = useQuery({
    enabled: !!skill && !!revisionId,
    queryKey: ["skills", workspace.id, skill?.id, "revision", revisionId],
    queryFn: async ({ signal }) => {
      const found = data(
        await client.http.GET("/api/v1/skill-revisions/{skill_revision_id}", {
          params: { path: { skill_revision_id: revisionId } },
          headers: workspaceHeaders(workspace.id),
          signal,
        }),
      );
      if (found.skill_id !== skill!.id)
        throw new Error(t("This version does not belong to this skill."));
      return found;
    },
  });
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
        { value: "versions", label: t("Versions"), count: `v${skill.version}` },
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
          resourceKey={skill.key}
          description={revision.data?.manifest.description}
          actions={
            <>
              {can("skill.revision.publish") && <ImportSkill skill={skill} />}
              <SkillMenu
                resource={query.data}
                revisionId={revisionId}
                version={revision.data?.version ?? skill.version}
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
                <span>v{revision.data.version}</span>
                <Timestamp value={revision.data.created_at} relative />
                <span>
                  {skillSource(revision.data.imported_from.kind) === "github"
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
