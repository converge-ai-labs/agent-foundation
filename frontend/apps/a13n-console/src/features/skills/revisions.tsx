import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { data, workspaceHeaders, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import styles from "../../shared/shared.module.css";
import { DownloadRevision } from "./archive";

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
  return (
    <div className={styles.stack}>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : (
        query.data && (
          <>
            <ResourceTable
              items={query.data.items}
              columns={[
                {
                  label: t("Version"),
                  render: (item) => (
                    <Link to={`?revision=${item.id}`}>
                      v{item.version}
                      {item.id === skill.current_revision_id && (
                        <small>{t("Current")}</small>
                      )}
                    </Link>
                  ),
                },
                {
                  label: t("Source"),
                  render: (item) =>
                    item.imported_from.kind === "github"
                      ? "GitHub"
                      : t("ZIP file"),
                },
                {
                  label: t("Files"),
                  render: (item) => item.manifest.files.length,
                },
                {
                  label: t("Created"),
                  render: (item) => <Timestamp value={item.created_at} />,
                },
                {
                  label: t("Actions"),
                  align: "right",
                  render: (item) => (
                    <DownloadRevision
                      revision={item}
                      filename={`${skill.key}-v${item.version}.zip`}
                    />
                  ),
                },
              ]}
            />
            <Pagination page={page} next={query.data.next_cursor} />
          </>
        )
      )}
    </div>
  );
}
