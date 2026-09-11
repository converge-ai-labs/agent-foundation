import { ResourceIdentity } from "../../shared/collection";
import { ProviderIcon } from "../../shared/provider-icon";
import { useResourceRows } from "../../shared/resource-modal";
import { ScopeBadge } from "../../shared/scope-badge";
import { ManageProvidersLink } from "../providers/manage-link";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { PageActions } from "../../shared/page-actions";
import styles from "../../shared/shared.module.css";
import { environmentApi, type EnvironmentScope } from "./api";
import { useEnvironmentTypes } from "./providers";
import { TemplateEditor } from "./template-editor";

export function EnvironmentTemplates({ scope }: { scope: EnvironmentScope }) {
  const client = useClient(),
    { can, organizationAdmin } = useAccess(),
    { t } = useTranslation(),
    page = useCursor(),
    api = environmentApi(client, scope),
    rows = useResourceRows<Schema["EnvironmentTemplate"]>(),
    providerTypes = useEnvironmentTypes();
  const query = useQuery({
    queryKey: ["environment-templates", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) => api.templates(signal, page.cursor),
  });
  const providers = useQuery({
    queryKey: ["environment-provider-options", scope.kind, scope.id],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const revisions = useQueries({
    queries: (query.data?.items ?? []).map((item) => ({
      queryKey: ["environment-revision", item.current_revision_id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        client.http
          .GET("/api/v1/environment-template-revisions/{revision_id}", {
            params: { path: { revision_id: item.current_revision_id } },
            signal,
          })
          .then(data),
    })),
  });
  const providerById = new Map(
    providers.data?.map((provider) => [provider.id, provider]),
  );
  const revisionById = new Map(
    revisions.flatMap((revision) =>
      revision.data ? [[revision.data.id, revision.data] as const] : [],
    ),
  );
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
      {rows.selected && (
        <TemplateEditor
          key={rows.selected.id}
          scope={
            rows.selected.workspace_id
              ? { kind: "workspace", id: rows.selected.workspace_id }
              : { kind: "organization", id: rows.selected.organization_id }
          }
          templateId={rows.selected.id}
          editable={rows.selected.workspace_id ? manage : organizationAdmin}
          {...rows.control}
        />
      )}
      <ErrorNotice error={query.error} />
      <ErrorNotice
        error={providers.error}
        retry={() => void providers.refetch()}
      />
      {query.isPending ? (
        <Loading />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            onRowActivate={rows.activate}
            columns={[
              {
                label: t("Name"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    description={item.description}
                    resourceId={item.id}
                  />
                ),
              },
              {
                label: t("Provider"),
                render: (item) => {
                  const revision = revisionById.get(item.current_revision_id);
                  const provider = revision
                    ? providerById.get(revision.provider_id)
                    : undefined;
                  return provider ? (
                    <div className="flex min-w-0 items-center gap-3">
                      <ProviderIcon key={provider.type} type={provider.type} />
                      <ResourceIdentity
                        name={provider.name}
                        resourceId={provider.id}
                        description={
                          providerTypes.data?.items.find(
                            (definition) => definition.type === provider.type,
                          )?.display_name ?? provider.type
                        }
                      />
                    </div>
                  ) : (
                    <span className="text-muted-foreground">
                      {t(
                        providers.isPending ||
                          revisions.some((entry) => entry.isPending)
                          ? "Loading…"
                          : "Provider unavailable",
                      )}
                    </span>
                  );
                },
              },
              {
                label: t("Scope"),
                tone: "muted",
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
