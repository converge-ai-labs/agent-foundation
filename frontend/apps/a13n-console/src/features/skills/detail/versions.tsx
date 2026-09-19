import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, workspaceHeaders, type Schema } from "../../../shared/api";
import {
  CollectionFooter,
  ListRow,
  ListRows,
  Pagination,
  useCursor,
} from "../../../shared/collection";
import { Confirm } from "../../../shared/dialogs";
import { ErrorNotice, Loading, Timestamp } from "../../../shared/feedback";
import { DownloadRevision } from "../archive";
import { SourceIcon, skillSource } from "../source";

/**
 * Published packages, newest first. Every version stays downloadable, and
 * any of them can become the default agents receive without a version pin.
 */
export function Revisions({
  skill,
  etag,
}: {
  skill: Schema["Skill"];
  etag?: string;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
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
                {revision.id === skill.default_revision_id && (
                  <span className="text-xs text-muted-foreground">
                    {" "}
                    {t("Default version")}
                  </span>
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
              <>
                {can("skill.revision.publish") &&
                  revision.id !== skill.default_revision_id && (
                    <Confirm
                      subject={`${skill.name} · v${revision.version}`}
                      triggerVariant="ghost"
                      title={t("Set as default")}
                      description={t(
                        "Future runs will use this version unless another version is selected.",
                      )}
                      trigger={t("Set as default")}
                      action={async () => {
                        if (!etag)
                          throw new Error(
                            t(
                              "Version information is unavailable. Reload this page.",
                            ),
                          );
                        await client.http
                          .POST(
                            "/api/v1/skills/{skill_id}/revisions/{skill_revision_id}/default",
                            {
                              params: {
                                path: {
                                  skill_id: skill.id,
                                  skill_revision_id: revision.id,
                                },
                                header: { "If-Match": etag },
                              },
                              headers: workspaceHeaders(workspace.id),
                            },
                          )
                          .then(data);
                        await cache.invalidateQueries({
                          queryKey: ["skills", workspace.id],
                        });
                      }}
                    />
                  )}
                <DownloadRevision
                  revision={revision}
                  filename={`${skill.key}-v${revision.version}.zip`}
                />
              </>
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
