import { ManageProvidersLink } from "../providers/manage-link";
import { Badge, FormField, Input } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { Layers } from "lucide-react";
import { useState } from "react";
import { PageActions } from "../../shared/page-actions";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess, useWorkspace } from "../../layout/workspace";
import { allPages, type Schema } from "../../shared/api";
import { Pagination, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
} from "../../shared/feedback";
import styles from "../../shared/shared.module.css";
import { modelApi, type ModelScope } from "./api";
import { ModelEditor, ModelTest } from "./model-editor";
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
  const groups = new Map<string, Schema["Model"][]>();
  for (const model of query.data?.items ?? []) {
    const group = groups.get(model.provider_id) ?? [];
    group.push(model);
    groups.set(model.provider_id, group);
  }
  const manage =
    scope.kind === "organization" ? organizationAdmin : can("models.manage");
  return (
    <div className={styles.stack}>
      <PageActions>{manage && <ModelEditor scope={scope} />}</PageActions>
      <ManageProvidersLink category="models" scope={scope.kind} />
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
          {Array.from(groups, ([providerId, models]) => {
            const provider = providers.data?.find(
              (item) => item.id === providerId,
            );
            return (
              <section className={modelStyles.providerGroup} key={providerId}>
                <header>
                  <span className={modelStyles.providerIcon}>
                    <Layers size={14} />
                  </span>
                  <h2>
                    {provider?.name ??
                      t(
                        providers.isPending
                          ? "Loading…"
                          : "Provider unavailable",
                      )}
                  </h2>
                  <span>{t("{{count}} models", { count: models.length })}</span>
                  {provider && (
                    <span className={modelStyles.providerState}>
                      <StateBadge
                        state={provider.enabled ? "enabled" : "disabled"}
                      />
                    </span>
                  )}
                </header>
                <div className={modelStyles.modelGrid}>
                  {models.map((item) => {
                    const owner: ModelScope = item.workspace_id
                      ? { kind: "workspace", id: item.workspace_id }
                      : { kind: "organization", id: organization.id };
                    return (
                      <article className={modelStyles.modelCard} key={item.id}>
                        <header>
                          <Layers size={16} strokeWidth={1.5} />
                          <h3>{item.name}</h3>
                          <StateBadge
                            state={item.enabled ? "enabled" : "disabled"}
                          />
                        </header>
                        <p>{item.description || item.upstream_model}</p>
                        <div className={modelStyles.modelDetails}>
                          <Badge variant={"secondary"}>
                            {t(
                              item.workspace_id ? "Workspace" : "Organization",
                            )}
                          </Badge>
                          <span title={t("Calling API")}>{item.model_api}</span>
                        </div>
                        <footer>
                          <code title={t("Model key")}>{item.key}</code>
                          {(item.workspace_id ? manage : organizationAdmin) && (
                            <div className={styles.actions}>
                              <ModelEditor scope={owner} modelId={item.id} />
                              <ModelTest scope={owner} modelId={item.id} />
                            </div>
                          )}
                        </footer>
                      </article>
                    );
                  })}
                </div>
              </section>
            );
          })}
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
