import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { representation } from "../../shared/api";
import { ResourceTable } from "../../shared/collection";
import { CopyableId } from "../../shared/copy";
import { Timestamp } from "../../shared/feedback";
import { Confirm } from "../../shared/form";
import { PageActions } from "../../shared/page-actions";
import styles from "../../shared/shared.module.css";
import { CreateWorkspace } from "./create-workspace";
export function Workspaces() {
  const { organization, workspaces } = useAccess(),
    client = useClient(),
    { t } = useTranslation();
  return (
    <div className={styles.stack}>
      <PageActions>
        <CreateWorkspace organizationId={organization.id} />
      </PageActions>
      <ResourceTable
        items={workspaces}
        columns={[
          {
            label: t("Name"),
            render: (item) => (
              <>
                <Link to={`/workspaces/${item.id}/settings`}>{item.name}</Link>
                <small>
                  <CopyableId value={item.id} />
                </small>
              </>
            ),
          },
          {
            label: t("Created"),
            render: (item) => <Timestamp value={item.created_at} />,
          },
          {
            label: t("Actions"),
            align: "right",
            render: (item) => (
              <Confirm
                title={t("Delete workspace")}
                description={t(
                  "This removes the workspace and revokes its access. This cannot be undone.",
                )}
                trigger={t("Delete")}
                danger
                action={async () => {
                  const latest = representation(
                    await client.http.GET("/api/v1/workspaces/{workspace_id}", {
                      params: { path: { workspace_id: item.id } },
                    }),
                  );
                  if (
                    latest.value.updated_at !== item.updated_at ||
                    !latest.etag
                  )
                    throw new Error(
                      t("This workspace changed. Reload before deleting it."),
                    );
                  await client.http.DELETE(
                    "/api/v1/workspaces/{workspace_id}",
                    {
                      params: {
                        path: { workspace_id: item.id },
                        header: { "If-Match": latest.etag },
                      },
                    },
                  );
                }}
              />
            ),
          },
        ]}
      />
    </div>
  );
}
