import { MenuItem } from "a13n-ui";
import { ArchiveIcon, StackIcon } from "@phosphor-icons/react";
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
            rowMenu={(item) =>
              item.archived_at ? null : (
                <DeleteWorkspace
                  workspace={item}
                  triggerElement={
                    <MenuItem closeOnClick={false} variant="destructive">
                      <ArchiveIcon size={14} />
                      {t("Archive")}
                    </MenuItem>
                  }
                />
              )
            }
            columns={[
              {
                label: t("Workspace"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    icon={<StackIcon size={15} aria-hidden="true" />}
                    name={item.name}
                    description={item.archived_at ? t("Archived") : undefined}
                    to={`${workspacePath(item)}/settings/general`}
                    resourceId={item.id}
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
 * Archiving a workspace leaves it read-only for good, so it is only safe from
 * the version the reader saw: the current representation is read again
 * before the request carries its ETag.
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
      title={t("Archive workspace")}
      description={t("The workspace becomes read-only. This cannot be undone.")}
      trigger={t("Archive workspace")}
      triggerElement={triggerElement}
      triggerVariant="outline"
      danger
      onSuccess={onSuccess}
      action={async () => {
        const latest = representation(
          await client.http.GET("/api/v1/workspaces/{workspace_id}", {
            params: { path: { workspace_id: workspace.id } },
          }),
        );
        if (latest.value.updated_at !== workspace.updated_at || !latest.etag)
          throw new Error(
            t("This workspace changed. Reload before archiving it."),
          );
        await client.http.POST("/api/v1/workspaces/{workspace_id}/archive", {
          params: {
            path: { workspace_id: workspace.id },
            header: { "If-Match": latest.etag },
          },
        });
      }}
    />
  );
}
