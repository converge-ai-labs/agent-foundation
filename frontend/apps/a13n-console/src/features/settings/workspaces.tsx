import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { representation } from "../../shared/api";
import { Timestamp } from "../../shared/feedback";
import { Confirm } from "../../shared/form";
import { Table } from "../../shared/collection";
import { CreateWorkspace } from "./create-workspace";
import styles from "../../shared/shared.module.css";
export function Workspaces() {
  const { organization, workspaces } = useAccess(),
    client = useClient(),
    { t } = useTranslation();
  return (
    <div className={styles.stack}>
      <div className={styles.toolbar}>
        <p className={styles.muted}>
          {t("Separate agents, resources, and access into workspaces.")}
        </p>
        <CreateWorkspace organizationId={organization.id} />
      </div>
      <Table
        items={workspaces}
        columns={[
          {
            label: t("Name"),
            render: (item) => (
              <Link to={`/workspaces/${item.id}/settings`}>
                {item.name}
                <small>{item.id}</small>
              </Link>
            ),
          },
          {
            label: t("Created"),
            render: (item) => <Timestamp value={item.created_at} />,
          },
          {
            label: t("Actions"),
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
