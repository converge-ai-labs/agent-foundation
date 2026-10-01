import { StackIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { ChoiceField } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  allPages,
  matchesSearch,
  matchingPage,
  type Schema,
} from "../../shared/api";
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
import { ProviderIcon } from "../../shared/identity";
import { Page, PageActions } from "../../shared/page";
import { ManageProvidersLink } from "../providers/manage-link";
import { environmentApi, environmentTemplates } from "./api";
import { useEnvironmentTypes } from "./providers";
import { TemplateEditor } from "./template-editor";

/** Reusable environment definitions: one row per template. */
export function EnvironmentTemplates() {
  const client = useClient(),
    { can, workspace } = useWorkspace(),
    { t } = useTranslation(),
    api = environmentApi(client, workspace.id),
    rows = useResourceRows<Schema["Template"]>(),
    providerTypes = useEnvironmentTypes();
  const [search, setSearch] = useState("");
  const [status, setStatus] = useState<"all" | "active" | "archived">("all");
  const term = search.trim().toLocaleLowerCase();
  const filtered = !!term || status !== "all";
  const page = useCursor({ term, status });
  const query = useQuery({
    queryKey: [
      "environment-templates",
      workspace.id,
      page.cursor,
      term,
      status,
    ],
    queryFn: ({ signal }) =>
      matchingPage(
        (cursor, limit) =>
          environmentTemplates(client, workspace.id, signal, cursor, limit),
        page.cursor,
        filtered
          ? (template) =>
              matchesSearch(term, template.name, template.description) &&
              (status === "all" || template.enabled === (status === "active"))
          : undefined,
      ),
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
    <Page
      title={t("Environment templates")}
      description={t(
        "Reusable templates for your agents' working environments.",
      )}
      toolbar={
        <Toolbar
          search={search}
          onSearchChange={setSearch}
          searchLabel={t("Search templates")}
          filters={
            <ChoiceField
              label={t("Status")}
              variant="filter"
              value={status}
              onValueChange={(value) =>
                setStatus(
                  value === "active" || value === "archived" ? value : "all",
                )
              }
              options={[
                { value: "all", label: t("All statuses") },
                { value: "active", label: t("state.active") },
                { value: "archived", label: t("state.archived") },
              ]}
            />
          }
        />
      }
    >
      <PageActions secondary>
        <ManageProvidersLink category="environments" />
      </PageActions>
      <PageActions>{manage && <TemplateEditor />}</PageActions>
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
            title={
              filtered
                ? t("No matching templates")
                : t("No environment templates")
            }
            description={
              filtered
                ? t("Change or clear the search and filters.")
                : t(
                    "Create a template, then choose it when starting a conversation.",
                  )
            }
            action={!filtered && manage ? <TemplateEditor /> : undefined}
          />
        )
      )}
    </Page>
  );
}
