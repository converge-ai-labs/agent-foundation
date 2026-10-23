import {
  Button,
  ChoiceField,
  StatusPill,
  Tooltip,
  TooltipPopup,
  TooltipTrigger,
} from "a13n-ui";
import { useQuery } from "@tanstack/react-query";
import { CubeIcon } from "@phosphor-icons/react";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { type Schema } from "../../shared/api";
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
import { Page, PageActions } from "../../shared/page";
import { ManageProvidersLink } from "../providers";
import { mediaDefaultsQuery, modelApi, workspaceProvidersQuery } from "./api";
import { ModelEditor } from "./model-editor";
import { mediaKinds, type MediaSelection } from "./media-understanding-fields";
import { inputCapabilities } from "./model-information";
import { ModelIcon, ModelProviderIcon, providerBrand } from "./model-icon";
import styles from "./models.module.css";

const FILTER_KEYS = ["q", "provider_id", "status"];

export function ModelsPage() {
  const { t } = useTranslation(),
    { can, workspace } = useWorkspace(),
    client = useClient(),
    [searchParams, setSearchParams] = useSearchParams();
  const api = modelApi(client, workspace.id);
  const committedQuery = searchParams.get("q") ?? "";
  const [search, setSearch] = useState(committedQuery);
  useEffect(() => setSearch(committedQuery), [committedQuery]);
  const providerId = searchParams.get("provider_id") ?? "";
  const requestedStatus = searchParams.get("status");
  const status =
    requestedStatus === "enabled" || requestedStatus === "disabled"
      ? requestedStatus
      : null;
  const enabled =
    status === "enabled" ? true : status === "disabled" ? false : undefined;
  const hasFilters = FILTER_KEYS.some((key) => searchParams.has(key));
  const page = useCursor({ committedQuery, providerId, enabled });
  const updateFilters = (patch: Record<string, string>) => {
    setSearchParams((previous) => {
      const next = new URLSearchParams(previous);
      for (const [key, value] of Object.entries(patch)) {
        if (value) next.set(key, value);
        else next.delete(key);
      }
      return next;
    });
  };
  const query = useQuery({
    queryKey: [
      "models",
      workspace.id,
      page.cursor,
      committedQuery,
      providerId,
      enabled,
    ],
    queryFn: ({ signal }) =>
      api.models(
        signal,
        page.cursor,
        committedQuery || undefined,
        providerId || undefined,
        enabled,
      ),
  });
  const providers = useQuery(workspaceProvidersQuery(client, workspace.id));
  const mediaDefaults = useQuery(mediaDefaultsQuery(client, workspace.id));
  const rows = useResourceRows<Schema["Model"]>();
  const { selected } = rows;
  const providerById = new Map(providers.data?.map((item) => [item.id, item]));
  const manage = can("write");
  const items = query.data?.items ?? [];
  const create = manage && (
    <ModelEditor
      onSaved={(model) => {
        setSearch(model.key);
        updateFilters({ q: model.key });
      }}
    />
  );
  return (
    <Page
      title={t("Models")}
      description={t("Choose the models your agents can use.")}
      toolbar={
        <Toolbar
          search={search}
          searchLabel={t("Search models")}
          onSearchChange={(value) => {
            setSearch(value);
            updateFilters({ q: value.trim() });
          }}
          filters={
            <>
              <span className={styles.filters}>
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
                      icon: <ModelProviderIcon provider={provider} />,
                    })),
                  ]}
                />
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
              </span>
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
      }
    >
      <PageActions secondary>
        <ManageProvidersLink category="models" />
      </PageActions>
      <PageActions>{create}</PageActions>
      {selected && (
        <ModelEditor
          key={selected.key}
          modelKey={selected.key}
          {...rows.control}
        />
      )}
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
            canActivateRow={() => manage}
            onRowActivate={rows.activate}
            columns={[
              {
                label: t("Model"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    resourceKey={item.key}
                    icon={
                      <ModelIcon
                        upstream={item.config.model_name}
                        catalogRef={item.catalog_ref}
                        provider={providerBrand(
                          providerById.get(item.provider_id),
                        )}
                        size={20}
                      />
                    }
                    description={item.key}
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
                      <ModelProviderIcon provider={provider} />
                      <span title={provider.name}>{provider.name}</span>
                    </span>
                  );
                },
              },
              {
                label: t("Capabilities"),
                render: (item) => (
                  <CapabilityMarks
                    model={item}
                    defaults={mediaDefaults.data?.value}
                  />
                ),
              },
              {
                label: t("Status"),
                // One line: an enabled model its provider can't serve reads as that state.
                render: (item) => {
                  const provider = providerById.get(item.provider_id);
                  return item.enabled && provider?.enabled === false ? (
                    <StatusPill
                      variant="warning"
                      title={t(
                        "Agents can't use this model until {{provider}} is enabled.",
                        { provider: provider.name },
                      )}
                    >
                      {t("Provider disabled")}
                    </StatusPill>
                  ) : (
                    <StatePill state={item.enabled ? "enabled" : "disabled"} />
                  );
                },
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
            count={t("{{count}} models on this page", { count: items.length })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        <Empty
          icon={<CubeIcon aria-hidden="true" />}
          title={t(hasFilters ? "No matching models" : "No models yet")}
          description={t(
            hasFilters
              ? "Change or clear the search and filters."
              : "Add a provider, then save a model alias for your agents.",
          )}
          action={!hasFilters && create}
        />
      )}
    </Page>
  );
}

/**
 * The inputs a model declares, as marks named on hover. A kind the model is
 * the workspace's media understanding default for is accented. There are four
 * kinds at most, so the marks always fit without a count.
 */
function CapabilityMarks({
  model,
  defaults,
}: {
  model: Schema["Model"];
  defaults?: MediaSelection;
}) {
  const { t } = useTranslation();
  const declared = inputCapabilities.filter(({ capability }) =>
    model.config.characteristics?.capabilities?.includes(capability),
  );
  if (!declared.length) return <span aria-hidden="true">—</span>;
  return (
    <span className={styles.capabilityMarks}>
      {declared.map(({ capability, label, Icon }) => {
        const role = mediaKinds.find(
          (entry) =>
            `${entry.kind}_understanding` === capability &&
            defaults?.[entry.kind] === model.key,
        )?.badge;
        const name = role ? `${t(label)} · ${t(role)}` : t(label);
        return (
          <Tooltip key={capability}>
            <TooltipTrigger
              render={
                <span
                  role="img"
                  aria-label={name}
                  className={styles.capabilityMark}
                  data-role={role ? "" : undefined}
                />
              }
            >
              <Icon size={16} aria-hidden="true" />
            </TooltipTrigger>
            <TooltipPopup>{name}</TooltipPopup>
          </Tooltip>
        );
      })}
    </span>
  );
}
