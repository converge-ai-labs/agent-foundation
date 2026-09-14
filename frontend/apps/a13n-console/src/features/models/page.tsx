import { useResourceRows } from "../../shared/resource-modal";
import { CopyableResourceKey } from "../../shared/copy";
import { ScopeBadge } from "../../shared/scope-badge";
import { ManageProvidersLink } from "../providers/manage-link";
import { Button, FormField, Input, SearchPicker } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { ProviderIcon } from "../../shared/provider-icon";
import { useEffect, useState } from "react";
import { useSearchParams } from "react-router";
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
  InlineLoading,
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
  const filterKeys = ["q", "provider_id", "scope", "status"];
  const hasFilters = filterKeys.some((key) => searchParams.has(key));
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
  return (
    <div className={styles.stack}>
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
      <div className="mb-1 flex flex-wrap items-center gap-2">
        <FormField
          className="w-full min-w-0 sm:w-80"
          label={t("Search models")}
          hideLabel
        >
          <Input
            placeholder={t("Name or model key…")}
            value={search}
            onChange={(event) => {
              const value = event.target.value;
              setSearch(value);
              updateFilters({ q: value.trim() });
            }}
            type="search"
            maxLength={128}
          />
        </FormField>
        <div className="w-48">
          <SearchPicker
            label={t("Provider")}
            placeholder={t("All providers")}
            emptyMessage={t("No matching providers")}
            groups={[
              {
                label: t("Providers"),
                options: [
                  { value: "all", label: t("All providers") },
                  ...(providers.data ?? []).map((provider) => ({
                    value: provider.id,
                    label: provider.name,
                    keywords: [provider.type],
                    icon: <ProviderIcon type={provider.type} />,
                  })),
                ],
              },
            ]}
            value={providerId || "all"}
            onValueChange={(value) =>
              updateFilters({ provider_id: value === "all" ? "" : value })
            }
          />
        </div>
        {scope.kind === "workspace" && (
          <div className="w-40">
            <SearchPicker
              label={t("Scope")}
              placeholder={t("All scopes")}
              emptyMessage={t("No results")}
              groups={[
                {
                  label: t("Scope"),
                  options: [
                    { value: "all", label: t("All scopes") },
                    { value: "workspace", label: t("Workspace") },
                    { value: "organization", label: t("Organization") },
                  ],
                },
              ]}
              value={ownerScope ?? "all"}
              onValueChange={(value) =>
                updateFilters({ scope: value === "all" ? "" : value })
              }
            />
          </div>
        )}
        <div className="w-40">
          <SearchPicker
            label={t("Status")}
            placeholder={t("All statuses")}
            emptyMessage={t("No results")}
            groups={[
              {
                label: t("Status"),
                options: [
                  { value: "all", label: t("All statuses") },
                  { value: "enabled", label: t("Enabled") },
                  { value: "disabled", label: t("Disabled") },
                ],
              },
            ]}
            value={status ?? "all"}
            onValueChange={(value) =>
              updateFilters({ status: value === "all" ? "" : value })
            }
          />
        </div>
        {hasFilters && (
          <Button
            variant="ghost"
            onClick={() => {
              setSearch("");
              updateFilters({ q: "", provider_id: "", scope: "", status: "" });
            }}
          >
            {t("Clear filters")}
          </Button>
        )}
      </div>
      {query.isPending ? (
        <Loading variant="table" columns={5} />
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
                  tone: "primary",
                  render: (item) => (
                    <ResourceIdentity
                      name={item.name}
                      resourceId={item.id}
                      resourceKey={item.key}
                      description={<CopyableResourceKey value={item.key} />}
                    />
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
                          resourceId={provider.id}
                        />
                      </div>
                    ) : (
                      <span className="text-muted-foreground">
                        {providers.isPending ? (
                          <InlineLoading width="7rem" />
                        ) : (
                          t("Provider unavailable")
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
