import { credentialMode } from "../../shared/provider-authentication";
import { useResourceRows } from "../../shared/resource-modal";
import { data, type Schema } from "../../shared/api";
import { ProviderIcon } from "../../shared/provider-icon";
import { ResourceIdentity } from "../../shared/collection";
import { ScopeBadge } from "../../shared/scope-badge";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { PageActions } from "../../shared/page-actions";
import styles from "../../shared/shared.module.css";
import { webProviderApi, type WebProviderScope } from "./api";
import { WebProviderEditor } from "./editor";

export function WebProviders({ scope }: { scope: WebProviderScope }) {
  const client = useClient(),
    { t } = useTranslation(),
    { can, organizationAdmin } = useAccess(),
    page = useCursor();
  const rows = useResourceRows<Schema["WebProvider"]>();
  const query = useQuery({
    queryKey: ["web-providers", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      webProviderApi(client, scope).providers(signal, page.cursor),
  });
  const definitions = useQuery({
    queryKey: ["web-provider-types"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/web-provider-types", { signal }).then(data),
  });
  const manage =
    scope.kind === "organization"
      ? organizationAdmin
      : can("web_provider.manage");
  return (
    <div className={styles.stack}>
      <PageActions>{manage && <WebProviderEditor scope={scope} />}</PageActions>
      {rows.selected && (
        <WebProviderEditor
          key={rows.selected.id}
          scope={scope}
          providerId={rows.selected.id}
          readOnly={
            !(
              manage &&
              (scope.kind === "organization" ||
                rows.selected.workspace_id === scope.id)
            )
          }
          {...rows.control}
        />
      )}
      {query.isPending ? (
        <Loading variant="table" columns={4} />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            onRowActivate={rows.activate}
            columns={[
              {
                label: t("Provider"),
                tone: "primary",
                render: (item) => (
                  <div className="flex min-w-0 items-center gap-3">
                    <ProviderIcon key={item.type} type={item.type} />
                    <ResourceIdentity
                      name={item.name}
                      description={item.type}
                      resourceId={item.id}
                    />
                  </div>
                ),
              },
              {
                label: t("Scope"),
                tone: "muted",
                render: (item) => (
                  <ScopeBadge workspaceId={item.workspace_id} />
                ),
              },
              {
                label: t("Credentials"),
                render: (item) =>
                  t(
                    credentialMode(
                      definitions.data?.items.find(
                        (definition) => definition.type === item.type,
                      ),
                      item.configuration,
                    ) !== "required"
                      ? "Not required"
                      : item.credential_configured
                        ? "Configured"
                        : "Not configured",
                  ),
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StateBadge state={item.enabled ? "enabled" : "disabled"} />
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        <Empty
          title={t("No Web Providers yet")}
          description={t("Add a Web Provider, then select it in your agent.")}
        />
      )}
    </div>
  );
}
