import { MenuItem } from "a13n-ui";
import { StackIcon, TrashIcon } from "@phosphor-icons/react";
import type { ReactElement } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { workspacePath } from "../../shared/paths";
import { representation, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  ResourceIdentity,
  ResourceTable,
} from "../../shared/collection";
import { Timestamp } from "../../shared/feedback";
import { Confirm } from "../../shared/dialogs";
import { PageActions } from "../../shared/page";
import styles from "../../shared/shared.module.css";
import { CreateWorkspace } from "./create-workspace";

export function Workspaces() {
  const { organization, workspaces } = useAccess(),
    { t } = useTranslation();
  const create = <CreateWorkspace organizationId={organization.id} />;
  return (
    <div className={styles.stack}>
      <PageActions>{create}</PageActions>
      {workspaces.length ? (
        <>
          <ResourceTable
            items={workspaces}
            caption={t("Workspaces")}
            rowMenuLabel={t("Workspace actions")}
            rowMenu={(item) => (
              <DeleteWorkspace
                workspace={item}
                triggerElement={
                  <MenuItem closeOnClick={false} variant="destructive">
                    <TrashIcon size={14} />
                    {t("Delete")}
                  </MenuItem>
                }
              />
            )}
            columns={[
              {
                label: t("Workspace"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    icon={<StackIcon size={15} aria-hidden="true" />}
                    name={item.name}
                    description={item.key}
                    to={`${workspacePath(item)}/settings/general`}
                    resourceId={item.id}
                    resourceKey={item.key}
                  />
                ),
              },
              {
                label: t("Created"),
                tone: "muted",
                render: (item) => (
                  <Timestamp value={item.created_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} workspaces", { count: workspaces.length })}
          />
        </>
      ) : (
        <Empty
          icon={<StackIcon aria-hidden="true" />}
          title={t("No workspaces")}
          description={t("Create your first workspace to start building.")}
          action={create}
        />
      )}
    </div>
  );
}

/**
 * Deleting a workspace is only safe from the version the reader saw, so the
 * current representation is read again before the request carries its ETag.
 */
export function DeleteWorkspace({
  workspace,
  triggerElement,
  onSuccess,
}: {
  workspace: Schema["Workspace"];
  triggerElement?: ReactElement;
  onSuccess?: () => void;
}) {
  const client = useClient(),
    { t } = useTranslation();
  return (
    <Confirm
      subject={workspace.name}
      title={t("Delete workspace")}
      description={t(
        "This removes the workspace and revokes its access. This cannot be undone.",
      )}
      trigger={t("Delete workspace")}
      triggerElement={triggerElement}
      triggerVariant="outline"
      danger
      onSuccess={onSuccess}
      action={async () => {
        const latest = representation(
          await client.http.GET("/api/v1/workspaces/{workspace}", {
            params: { path: { workspace: workspace.id } },
          }),
        );
        if (latest.value.updated_at !== workspace.updated_at || !latest.etag)
          throw new Error(
            t("This workspace changed. Reload before deleting it."),
          );
        await client.http.DELETE("/api/v1/workspaces/{workspace}", {
          params: {
            path: { workspace: workspace.id },
            header: { "If-Match": latest.etag },
          },
        });
      }}
    />
  );
}
