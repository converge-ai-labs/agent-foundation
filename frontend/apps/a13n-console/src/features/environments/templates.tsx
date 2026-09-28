import { StackIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, type Schema } from "../../shared/api";
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
import { ProviderIcon } from "../../shared/identity";
import { PageActions } from "../../shared/page";
import styles from "../../shared/shared.module.css";
import { ManageProvidersLink } from "../providers/manage-link";
import { environmentApi, environmentTemplates } from "./api";
import { useEnvironmentTypes } from "./providers";
import { TemplateEditor } from "./template-editor";

/** Reusable environment definitions: one row per template. */
export function EnvironmentTemplates() {
  const client = useClient(),
    { can, workspace } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor(),
    api = environmentApi(client, workspace.id),
    rows = useResourceRows<Schema["Template"]>(),
    providerTypes = useEnvironmentTypes();
  const query = useQuery({
    queryKey: ["environment-templates", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      environmentTemplates(client, workspace.id, signal, page.cursor),
  });
  const providers = useQuery({
    queryKey: ["environment-provider-options", workspace.id],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const providerById = new Map(
    providers.data?.map((provider) => [provider.id, provider]),
  );
  const resolving = providers.isPending;
  const manage = can("write");
  function providerOf(template: Schema["Template"]) {
    return providerById.get(template.provider_id);
  }
  function providerName(provider?: Schema["Provider"]) {
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
        <ManageProvidersLink category="environments" />
        {manage && <TemplateEditor />}
      </PageActions>
      {rows.selected && (
        <TemplateEditor
          key={rows.selected.id}
          templateId={rows.selected.id}
          editable={manage}
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
                label: t("Version"),
                render: (item) => `v${item.version}`,
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StatePill state={item.enabled ? "active" : "archived"} />
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
            action={manage ? <TemplateEditor /> : undefined}
          />
        )
      )}
    </div>
  );
}
