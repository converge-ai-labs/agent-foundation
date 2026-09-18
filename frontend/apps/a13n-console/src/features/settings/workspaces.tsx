import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { workspacePath } from "../../shared/paths";
import { representation } from "../../shared/api";
import { ResourceTable } from "../../shared/collection";
import { CopyableId } from "../../shared/identity";
import { Timestamp } from "../../shared/feedback";
import { Confirm } from "../../shared/dialogs";
import { PageActions } from "../../shared/page";
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
            tone: "primary",
            render: (item) => (
              <>
                <Link to={`${workspacePath(item)}/settings`}>{item.name}</Link>
                <small>
                  <CopyableId value={item.id} />
                </small>
              </>
            ),
          },
          {
            label: t("Created"),
            tone: "muted",
            render: (item) => <Timestamp value={item.created_at} />,
          },
          {
            label: t("Actions"),
            align: "right",
            render: (item) => (
              <Confirm
                subject={item.name}
                triggerVariant="ghost"
                title={t("Delete workspace")}
                description={t(
                  "This removes the workspace and revokes its access. This cannot be undone.",
                )}
                trigger={t("Delete")}
                danger
                action={async () => {
                  const latest = representation(
                    await client.http.GET("/api/v1/workspaces/{workspace}", {
                      params: { path: { workspace: item.id } },
                    }),
                  );
                  if (
                    latest.value.updated_at !== item.updated_at ||
                    !latest.etag
                  )
                    throw new Error(
                      t("This workspace changed. Reload before deleting it."),
                    );
                  await client.http.DELETE("/api/v1/workspaces/{workspace}", {
                    params: {
                      path: { workspace: item.id },
                      header: { "If-Match": latest.etag },
                    },
                  });
                }}
              />
            ),
          },
        ]}
      />
    </div>
  );
}
