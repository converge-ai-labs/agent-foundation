import { ResourceIdentity } from "../../shared/collection";
import { ScopeBadge } from "../../shared/scope-badge";
import { ManageProvidersLink } from "../providers/manage-link";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { PageActions } from "../../shared/page-actions";
import styles from "../../shared/shared.module.css";
import { environmentApi, type EnvironmentScope } from "./api";
import { TemplateEditor } from "./template-editor";

export function EnvironmentTemplates({ scope }: { scope: EnvironmentScope }) {
  const client = useClient(),
    { can, organizationAdmin } = useAccess(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["environment-templates", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      environmentApi(client, scope).templates(signal, page.cursor),
  });
  const manage =
    scope.kind === "organization"
      ? organizationAdmin
      : can("environment_template.manage");
  return (
    <div className={styles.stack}>
      <PageActions>
        <ManageProvidersLink category="environments" scope={scope.kind} />
        {manage && <TemplateEditor scope={scope} />}
      </PageActions>
      <ErrorNotice error={query.error} />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("Name"),
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    description={item.description}
                  />
                ),
              },
              {
                label: t("Scope"),
                render: (item) => (
                  <ScopeBadge workspaceId={item.workspace_id} />
                ),
              },
              { label: t("Version"), render: (item) => `v${item.version}` },
              {
                label: t("Status"),
                render: (item) => (
                  <StateBadge
                    state={item.archived_at ? "archived" : "active"}
                  />
                ),
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) => (
                  <TemplateEditor
                    scope={
                      item.workspace_id
                        ? { kind: "workspace", id: item.workspace_id }
                        : { kind: "organization", id: item.organization_id }
                    }
                    templateId={item.id}
                    editable={item.workspace_id ? manage : organizationAdmin}
                  />
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        !query.error && (
          <Empty
            title={t("No environment templates")}
            description={t(
              "Create a recipe, then choose it when starting a conversation.",
            )}
          />
        )
      )}
    </div>
  );
}
