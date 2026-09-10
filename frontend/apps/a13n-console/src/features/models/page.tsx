import { useResourceRows } from "../../shared/resource-modal";
import { ScopeBadge } from "../../shared/scope-badge";
import { ManageProvidersLink } from "../providers/manage-link";
import { FormField, Input } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { ProviderIcon } from "../../shared/provider-icon";
import { useState } from "react";
import { PageActions } from "../../shared/page-actions";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess, useWorkspace } from "../../layout/workspace";
import { allPages, type Schema } from "../../shared/api";
import {
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
} from "../../shared/feedback";
import styles from "../../shared/shared.module.css";
import { modelApi, type ModelScope } from "./api";
import { ModelEditor } from "./model-editor";
import modelStyles from "./models.module.css";

export function ModelsPage() {
  const { t } = useTranslation(),
    { workspace } = useWorkspace();
  return (
    <Page
      title={t("Models")}
      description={t("Choose the models your agents can use.")}
    >
      <Models scope={{ kind: "workspace", id: workspace.id }} />
    </Page>
  );
}
export function Models({ scope }: { scope: ModelScope }) {
  const { t } = useTranslation(),
    { can, organization, organizationAdmin } = useAccess(),
    client = useClient(),
    page = useCursor(),
    [search, setSearch] = useState("");
  const api = modelApi(client, scope);
  const query = useQuery({
    queryKey: ["models", scope.kind, scope.id, page.cursor, search],
    queryFn: ({ signal }) =>
      api.models(signal, page.cursor, search || undefined),
  });
  const providers = useQuery({
    queryKey: ["model-provider-choices", scope.kind, scope.id],
    queryFn: ({ signal }) =>
      allPages((cursor) => api.providers(signal, cursor)),
  });
  const rows = useResourceRows<Schema["Model"]>();
  const { selected } = rows;
  const providerById = new Map(providers.data?.map((item) => [item.id, item]));
  const manage =
    scope.kind === "organization" ? organizationAdmin : can("models.manage");
  return (
    <div className={styles.stack}>
      <PageActions>
        <ManageProvidersLink category="models" scope={scope.kind} />
        {manage && (
          <ModelEditor
            scope={scope}
            onSaved={(model) => {
              setSearch(model.key);
              page.reset();
            }}
          />
        )}
      </PageActions>
      {selected && (
        <ModelEditor
          key={selected.id}
          scope={
            selected.workspace_id
              ? { kind: "workspace", id: selected.workspace_id }
              : { kind: "organization", id: organization.id }
          }
          modelId={selected.id}
          {...rows.control}
        />
      )}
      <div className={styles.filters}>
        <FormField
          className="min-w-0 w-full"
          label={t("Search models")}
          hideLabel={true}
        >
          <Input
            placeholder={t("Name or model key…")}
            value={search}
            onChange={(event) => {
              setSearch(event.target.value);
              page.reset();
            }}
            type="search"
          />
        </FormField>
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.items.length ? (
        <>
          <ErrorNotice
            error={providers.error}
            retry={() => void providers.refetch()}
          />
          <div className={`${modelStyles.listTable} a13n-scrollbar`}>
            <ResourceTable
              caption={t("Models")}
              items={query.data.items}
              canActivateRow={(item) =>
                item.workspace_id ? manage : organizationAdmin
              }
              onRowActivate={rows.activate}
              columns={[
                {
                  label: t("Model"),
                  render: (item) => (
                    <ResourceIdentity name={item.name} description={item.key} />
                  ),
                },
                {
                  label: t("Provider"),
                  render: (item) => {
                    const provider = providerById.get(item.provider_id);
                    return provider ? (
                      <div className="flex min-w-0 items-center gap-3">
                        <ProviderIcon
                          key={provider.type}
                          type={provider.type}
                        />
                        <ResourceIdentity
                          name={provider.name}
                          description={provider.type}
                        />
                      </div>
                    ) : (
                      <span className="text-muted-foreground">
                        {t(
                          providers.isPending
                            ? "Loading…"
                            : "Provider unavailable",
                        )}
                      </span>
                    );
                  },
                },
                {
                  label: t("Scope"),
                  render: (item) => (
                    <ScopeBadge workspaceId={item.workspace_id} />
                  ),
                },
                {
                  label: t("Status"),
                  render: (item) => (
                    <div>
                      <StateBadge
                        state={item.enabled ? "enabled" : "disabled"}
                      />
                      {providerById.get(item.provider_id)?.enabled ===
                        false && <small>{t("Provider disabled")}</small>}
                    </div>
                  ),
                },
              ]}
            />
          </div>
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        <Empty
          title={t("No models yet")}
          description={t(
            "Add a provider, then save a model alias for your agents.",
          )}
        />
      )}
    </div>
  );
}
