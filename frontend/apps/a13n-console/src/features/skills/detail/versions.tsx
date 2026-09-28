import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, ifMatch, type Schema } from "../../../shared/api";
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
import { revisionsQuery } from "../revisions";
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
  const query = useQuery(
    revisionsQuery(client, skill.workspace_id, skill.id, page.cursor),
  );
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
            icon={<SourceIcon kind={revision.config.source.kind} size={15} />}
            name={
              <>
                <Link to={`?revision=${revision.id}`}>v{revision.number}</Link>
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
                {skillSource(revision.config.source.kind) === "github"
                  ? "GitHub"
                  : t("ZIP")}
                {" · "}
                {t("{{count}} files", {
                  count: revision.config.files.length,
                })}
              </>
            }
            actions={
              <>
                {can("write") &&
                  !skill.archived_at &&
                  revision.id !== skill.default_revision_id && (
                    <Confirm
                      subject={`${skill.name} · v${revision.number}`}
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
                        await client
                          .workspace(skill.workspace_id)
                          .POST(
                            "/api/v1/skills/{skill_id}/revisions/{revision_id}/set-default",
                            {
                              params: {
                                path: {
                                  skill_id: skill.id,
                                  revision_id: revision.id,
                                },
                              },
                              headers: ifMatch(etag),
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
                  filename={`${revision.config.name}-v${revision.number}.zip`}
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
