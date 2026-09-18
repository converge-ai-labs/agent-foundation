import { StatusPill } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../../auth/context";
import { data, workspaceHeaders, type Schema } from "../../../shared/api";
import {
  CollectionFooter,
  ListRow,
  ListRows,
  Pagination,
  useCursor,
} from "../../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../../shared/feedback";
import { DownloadRevision } from "../archive";
import { SourceIcon, skillSource } from "../source";

/** Published packages, newest first. Every version stays downloadable. */
export function Revisions({ skill }: { skill: Schema["Skill"] }) {
  const client = useClient(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: [
      "skills",
      skill.workspace_id,
      skill.id,
      "revisions",
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/skills/{skill_id}/revisions", {
          params: {
            path: { skill_id: skill.id },
            query: { cursor: page.cursor },
          },
          headers: workspaceHeaders(skill.workspace_id),
          signal,
        })
        .then(data),
  });
  if (query.isPending) return <Loading variant="list" rows={4} />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return (
    <>
      <ListRows>
        {query.data.items.map((revision) => (
          <ListRow
            key={revision.id}
            icon={<SourceIcon kind={revision.imported_from.kind} size={15} />}
            name={
              <>
                <Link to={`?revision=${revision.id}`}>v{revision.version}</Link>
                {revision.id === skill.current_revision_id && (
                  <>
                    {" "}
                    <StatusPill variant="success">{t("Current")}</StatusPill>
                  </>
                )}
              </>
            }
            secondary={
              <>
                <Timestamp value={revision.created_at} relative />
                {" · "}
                {skillSource(revision.imported_from.kind) === "github"
                  ? "GitHub"
                  : t("ZIP")}
                {" · "}
                {t("{{count}} files", {
                  count: revision.manifest.files.length,
                })}
              </>
            }
            actions={
              <DownloadRevision
                revision={revision}
                filename={`${skill.key}-v${revision.version}.zip`}
              />
            }
          />
        ))}
      </ListRows>
      <CollectionFooter
        count={t("{{count}} versions on this page", {
          count: query.data.items.length,
        })}
      >
        <Pagination page={page} next={query.data.next_cursor} />
      </CollectionFooter>
    </>
  );
}
