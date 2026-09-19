import { StackIcon } from "@phosphor-icons/react";
import { useQueries, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import { useResourceRows } from "../../shared/dialogs";
import {
  ErrorNotice,
  InlineLoading,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { ProviderIcon, ScopeBadge } from "../../shared/identity";
import { PageActions } from "../../shared/page";
import styles from "../../shared/shared.module.css";
import { ManageProvidersLink } from "../providers/manage-link";
import { environmentApi, type EnvironmentScope } from "./api";
import { useEnvironmentTypes } from "./providers";
import { TemplateEditor } from "./template-editor";

/** Reusable environment definitions: one row per template with its default revision. */
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
      queryKey: ["environment-revision", item.default_revision_id],
      queryFn: ({ signal }: { signal: AbortSignal }) =>
        client.http
          .GET("/api/v1/environment-template-revisions/{revision_id}", {
            params: { path: { revision_id: item.default_revision_id } },
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
  const resolving =
    providers.isPending || revisions.some((entry) => entry.isPending);
  const manage =
    scope.kind === "organization"
      ? organizationAdmin
      : can("environment_template.manage");
  function providerOf(template: Schema["EnvironmentTemplate"]) {
    const revision = revisionById.get(template.default_revision_id);
    return revision ? providerById.get(revision.provider_id) : undefined;
  }
  function providerName(provider?: Schema["EnvironmentProvider"]) {
    if (!provider) return undefined;
    return (
      providerTypes.data?.items.find(
        (definition) => definition.type === provider.type,
      )?.display_name ?? provider.name
    );
  }
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
        <Loading variant="table" columns={5} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            caption={t("Environment templates")}
            items={query.data.items}
            onRowActivate={rows.activate}
            columns={[
              {
                label: t("Template"),
                tone: "primary",
                render: (item) => {
                  const provider = providerOf(item);
                  return (
                    <ResourceIdentity
                      name={item.name}
                      resourceId={item.id}
                      icon={
                        provider ? (
                          <ProviderIcon
                            key={provider.type}
                            type={provider.type}
                          />
                        ) : (
                          <StackIcon
                            aria-hidden="true"
                            className="size-4 text-muted-foreground"
                          />
                        )
                      }
                      description={
                        providerName(provider) ??
                        (resolving ? (
                          <InlineLoading width="6rem" />
                        ) : (
                          t("Provider unavailable")
                        ))
                      }
                    />
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
              {
                label: t("Version"),
                render: (item) => `v${item.version}`,
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StatePill state={item.archived_at ? "archived" : "active"} />
                ),
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (item) => (
                  <Timestamp value={item.updated_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} templates on this page", {
              count: query.data.items.length,
            })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        !query.error && (
          <Empty
            icon={<StackIcon aria-hidden="true" />}
            title={t("No environment templates")}
            description={t(
              "Create a template, then choose it when starting a conversation.",
            )}
            action={manage ? <TemplateEditor scope={scope} /> : undefined}
          />
        )
      )}
    </div>
  );
}
