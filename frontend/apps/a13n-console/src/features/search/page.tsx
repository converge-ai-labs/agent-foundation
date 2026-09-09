import { useQuery } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess, useWorkspace } from "../../layout/workspace";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
} from "../../shared/feedback";
import { PageActions } from "../../shared/page-actions";
import styles from "../../shared/shared.module.css";
import { searchApi, type SearchScope } from "./api";
import { SearchProviderEditor, SearchProviderTest } from "./editor";

export function SearchProvidersPage() {
  const { workspace } = useWorkspace(),
    { t } = useTranslation();
  return (
    <Page
      title={t("Search accounts")}
      description={t(
        "Connect a search account once and reuse it across agents.",
      )}
    >
      <SearchProviders scope={{ kind: "workspace", id: workspace.id }} />
    </Page>
  );
}
export function SearchProviders({ scope }: { scope: SearchScope }) {
  const client = useClient(),
    { t } = useTranslation(),
    { can, organizationAdmin } = useAccess(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["search-providers", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      searchApi(client, scope).providers(signal, page.cursor),
  });
  const manage =
    scope.kind === "organization"
      ? organizationAdmin
      : can("search_provider.manage");
  return (
    <div className={styles.stack}>
      <PageActions>
        {manage && <SearchProviderEditor scope={scope} />}
      </PageActions>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("Account"),
                render: (item) => (
                  <>
                    <strong>{item.name}</strong>
                    <small>{item.type}</small>
                  </>
                ),
              },
              {
                label: t("Scope"),
                render: (item) =>
                  t(item.workspace_id ? "Workspace" : "Organization"),
              },
              {
                label: t("Credentials"),
                render: (item) =>
                  t(
                    item.credential_configured
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
              {
                label: t("Actions"),
                render: (item) => (
                  <div className={styles.actions}>
                    {manage &&
                      (scope.kind === "organization" ||
                        item.workspace_id === scope.id) && (
                        <SearchProviderEditor
                          scope={scope}
                          providerId={item.id}
                        />
                      )}
                    {manage && (
                      <SearchProviderTest
                        scope={scope}
                        providerId={item.id}
                        disabled={!item.enabled}
                      />
                    )}
                    <SearchReferences scope={scope} providerId={item.id} />
                  </div>
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        <Empty
          title={t("No search accounts yet")}
          description={t(
            "Add a Brave or Exa account, then select it in your agent.",
          )}
        />
      )}
    </div>
  );
}
function SearchReferences({
  scope,
  providerId,
}: {
  scope: SearchScope;
  providerId: string;
}) {
  const [open, setOpen] = useState(false),
    { t } = useTranslation(),
    client = useClient(),
    page = useCursor();
  const query = useQuery({
    queryKey: [
      "search-provider-references",
      scope.kind,
      scope.id,
      providerId,
      page.cursor,
    ],
    enabled: open,
    queryFn: ({ signal }) =>
      searchApi(client, scope).references(providerId, signal, page.cursor),
  });
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Agent references")}
      description={t(
        "Current and retained revisions visible to you. Disablement can affect these agents.",
      )}
      closeLabel={t("Close")}
      trigger={
        <Button type="button" variant="outline" size="sm">
          {t("References")}
        </Button>
      }
    >
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : (
        <>
          <ResourceTable
            items={(query.data?.items ?? []).map((item) => ({
              ...item,
              id: item.agent_revision_id,
            }))}
            columns={[
              {
                label: t("Agent"),
                render: (item) => <code>{item.agent_id}</code>,
              },
              {
                label: t("Revision"),
                render: (item) => (
                  <>
                    <code>{item.agent_revision_id}</code> · v{item.version}{" "}
                    {item.is_current && t("Current")}
                  </>
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data?.next_cursor} />
        </>
      )}
    </ModalFrame>
  );
}
