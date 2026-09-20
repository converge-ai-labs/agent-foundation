import { Button, ChoiceField, StatusPill } from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { CpuIcon } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { useAccess, useWorkspace } from "../../layout/workspace";
import { allPages, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  Toolbar,
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
import { Page, PageActions } from "../../shared/page";
import { ManageProvidersLink } from "../providers";
import { modelApi, type ModelScope } from "./api";
import { ModelEditor } from "./model-editor";
import { MediaUnderstandingDefaults } from "./media-understanding";
import { ModelIcon } from "./model-icon";
import styles from "./models.module.css";

const FILTER_KEYS = ["q", "provider_id", "scope", "status"];

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

/** The models collection, also embedded in organization settings. */
export function Models({ scope }: { scope: ModelScope }) {
  const { t } = useTranslation(),
    { can, organization, organizationAdmin } = useAccess(),
    client = useClient(),
    page = useCursor(),
    [searchParams, setSearchParams] = useSearchParams();
  const api = modelApi(client, scope);
  const committedQuery = searchParams.get("q") ?? "";
  const [search, setSearch] = useState(committedQuery);
  useEffect(() => setSearch(committedQuery), [committedQuery]);
  const providerId = searchParams.get("provider_id") ?? "";
  const requestedScope = searchParams.get("scope");
  const ownerScope =
    scope.kind === "workspace" &&
    (requestedScope === "organization" || requestedScope === "workspace")
      ? requestedScope
      : null;
  const requestedStatus = searchParams.get("status");
  const status =
    requestedStatus === "enabled" || requestedStatus === "disabled"
      ? requestedStatus
      : null;
  const enabled =
    status === "enabled" ? true : status === "disabled" ? false : undefined;
  const hasFilters = FILTER_KEYS.some((key) => searchParams.has(key));
  const updateFilters = (patch: Record<string, string>) => {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      for (const [key, value] of Object.entries(patch)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    });
    page.reset();
  };
  const query = useQuery({
    queryKey: [
      "models",
      scope.kind,
      scope.id,
      page.cursor,
      committedQuery,
      providerId,
      enabled,
      ownerScope,
    ],
    queryFn: ({ signal }) =>
      api.models(
        signal,
        page.cursor,
        committedQuery || undefined,
        providerId || undefined,
        enabled,
        ownerScope ?? undefined,
      ),
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
  const items = query.data?.items ?? [];
  return (
    <div className={styles.list}>
      <PageActions>
        <ManageProvidersLink category="models" scope={scope.kind} />
        {manage && (
          <ModelEditor
            scope={scope}
            onSaved={(model) => {
              setSearch(model.key);
              updateFilters({ q: model.key });
            }}
          />
        )}
      </PageActions>
      {scope.kind === "workspace" && (
        <MediaUnderstandingDefaults key={scope.id} />
      )}
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
      <Toolbar
        search={search}
        searchLabel={t("Search models")}
        onSearchChange={(value) => {
          setSearch(value);
          updateFilters({ q: value.trim() });
        }}
        filters={
          <>
            <ChoiceField
              label={t("Provider")}
              variant="filter"
              value={providerId || "all"}
              onValueChange={(value) =>
                updateFilters({ provider_id: value === "all" ? "" : value })
              }
              options={[
                { value: "all", label: t("All providers") },
                ...(providers.data ?? []).map((provider) => ({
                  value: provider.id,
                  label: provider.name,
                  icon: <ProviderIcon type={provider.type} />,
                })),
              ]}
            />
            {scope.kind === "workspace" && (
              <ChoiceField
                label={t("Scope")}
                variant="filter"
                value={ownerScope ?? "all"}
                onValueChange={(value) =>
                  updateFilters({ scope: value === "all" ? "" : value })
                }
                options={[
                  { value: "all", label: t("All scopes") },
                  { value: "workspace", label: t("Workspace") },
                  { value: "organization", label: t("Organization") },
                ]}
              />
            )}
            <ChoiceField
              label={t("Status")}
              variant="filter"
              value={status ?? "all"}
              onValueChange={(value) =>
                updateFilters({ status: value === "all" ? "" : value })
              }
              options={[
                { value: "all", label: t("All statuses") },
                { value: "enabled", label: t("Enabled") },
                { value: "disabled", label: t("Disabled") },
              ]}
            />
            {hasFilters && (
              <Button
                type="button"
                size="sm"
                variant="ghost"
                onClick={() => {
                  setSearch("");
                  updateFilters({
                    q: "",
                    provider_id: "",
                    scope: "",
                    status: "",
                  });
                }}
              >
                {t("Clear")}
              </Button>
            )}
          </>
        }
      />
      <ErrorNotice
        error={providers.error}
        retry={() => void providers.refetch()}
      />
      {query.isPending ? (
        <Loading variant="table" columns={5} />
      ) : query.error ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : items.length ? (
        <>
          <ResourceTable
            className={styles.listTable}
            caption={t("Models")}
            items={items}
            canActivateRow={(item) =>
              item.workspace_id ? manage : organizationAdmin
            }
            onRowActivate={rows.activate}
            columns={[
              {
                label: t("Model"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    resourceId={item.id}
                    resourceKey={item.key}
                    icon={
                      <ModelIcon
                        upstream={item.upstream_model}
                        catalogRef={item.catalog_ref}
                        provider={providerById.get(item.provider_id)?.type}
                        size={20}
                      />
                    }
                    description={
                      <span className={styles.modelMeta}>
                        <span title={item.key}>{item.key}</span>
                        {scope.kind === "workspace" && !item.workspace_id && (
                          <ScopeBadge workspaceId={item.workspace_id} />
                        )}
                      </span>
                    }
                  />
                ),
              },
              {
                label: t("Provider"),
                render: (item) => {
                  const provider = providerById.get(item.provider_id);
                  if (!provider)
                    return providers.isPending ? (
                      <InlineLoading width="7rem" />
                    ) : (
                      <span className={styles.chip}>
                        {t("Provider unavailable")}
                      </span>
                    );
                  return (
                    <span className={styles.providerCell}>
                      <ProviderIcon type={provider.type} />
                      <span title={provider.name}>{provider.name}</span>
                    </span>
                  );
                },
              },
              {
                label: t("Capabilities"),
                render: (item) => {
                  const labels = capabilityLabels(item.declarations);
                  if (!labels.length) return <span aria-hidden="true">—</span>;
                  return (
                    <span className={styles.chips}>
                      {labels.map((label) => (
                        <span key={label} className={styles.chip}>
                          {t(label)}
                        </span>
                      ))}
                    </span>
                  );
                },
              },
              {
                label: t("Status"),
                render: (item) => (
                  <span className={styles.statusCell}>
                    <StatePill state={item.enabled ? "enabled" : "disabled"} />
                    {providerById.get(item.provider_id)?.enabled === false && (
                      <StatusPill variant="warning">
                        {t("Provider disabled")}
                      </StatusPill>
                    )}
                  </span>
                ),
              },
              {
                label: t("Updated"),
                tone: "muted",
                align: "right",
                render: (item) => (
                  <Timestamp value={item.updated_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} models on this page", { count: items.length })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        <Empty
          icon={<CpuIcon aria-hidden="true" />}
          title={t(hasFilters ? "No matching models" : "No models yet")}
          description={t(
            hasFilters
              ? "Change or clear the search and filters."
              : "Add a provider, then save a model alias for your agents.",
          )}
        />
      )}
    </div>
  );
}

/** At most three neutral chips; the rest of the declarations stay in the editor. */
function capabilityLabels(declarations?: Schema["ModelDeclarations-Output"]) {
  const labels: string[] = [];
  if (declarations?.supports_tools) labels.push("Tools");
  if (declarations?.capabilities?.includes("image_understanding"))
    labels.push("Vision");
  if (declarations?.structured_output) labels.push("Structured");
  return labels;
}
