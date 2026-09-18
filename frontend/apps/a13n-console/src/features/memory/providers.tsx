import { Identifier } from "../../shared/copy";
import { useResourceRows } from "../../shared/resource-modal";
import type { Schema } from "../../shared/api";
import { ProviderIcon } from "../../shared/provider-icon";
import { ResourceIdentity } from "../../shared/collection";
import { ScopeBadge } from "../../shared/scope-badge";
import { useQuery } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { PageActions } from "../../shared/page-actions";
import styles from "../../shared/shared.module.css";
import { memoryProviderApi, type MemoryProviderScope } from "./providers-api";
import { MemoryProviderEditor } from "./editor";

export function MemoryProviders({ scope }: { scope: MemoryProviderScope }) {
  const client = useClient(),
    { t } = useTranslation(),
    { can, organizationAdmin } = useAccess(),
    page = useCursor();
  const rows = useResourceRows<Schema["MemoryProvider"]>();
  const query = useQuery({
    queryKey: ["memory-providers", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      memoryProviderApi(client, scope).providers(signal, page.cursor),
  });
  const manage =
    scope.kind === "organization"
      ? organizationAdmin
      : can("memory_provider.manage");
  return (
    <div className={styles.stack}>
      <PageActions>
        {manage && <MemoryProviderEditor scope={scope} />}
      </PageActions>
      {rows.selected && (
        <MemoryProviderEditor
          key={rows.selected.id}
          scope={scope}
          providerId={rows.selected.id}
          readOnly={
            !(
              manage &&
              (scope.kind === "organization" ||
                rows.selected.workspace_id === scope.id)
            )
          }
          {...rows.control}
          extra={
            <div className={styles.actions}>
              <MemoryReferences scope={scope} providerId={rows.selected.id} />
            </div>
          }
        />
      )}
      {query.isPending ? (
        <Loading variant="table" columns={4} />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            onRowActivate={rows.activate}
            columns={[
              {
                label: t("Provider"),
                tone: "primary",
                render: (item) => (
                  <div className="flex min-w-0 items-center gap-3">
                    <ProviderIcon key={item.type} type={item.type} />
                    <ResourceIdentity
                      name={item.name}
                      description={item.type}
                      resourceId={item.id}
                    />
                  </div>
                ),
              },
              {
                label: t("Scope"),
                tone: "muted",
                render: (item) => (
                  <ScopeBadge workspaceId={item.workspace_id} />
                ),
              },
              {
                label: t("Credentials"),
                render: (item) =>
                  t(
                    item.type === "a13n.filesystem"
                      ? "Not required"
                      : item.credential_configured
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
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        <Empty
          title={t("No memory providers yet")}
          description={t(
            "Add a memory provider, then select it in your agent.",
          )}
        />
      )}
    </div>
  );
}
function MemoryReferences({
  scope,
  providerId,
}: {
  scope: MemoryProviderScope;
  providerId: string;
}) {
  const [open, setOpen] = useState(false),
    { t } = useTranslation(),
    client = useClient(),
    page = useCursor();
  const query = useQuery({
    queryKey: [
      "memory-provider-references",
      scope.kind,
      scope.id,
      providerId,
      page.cursor,
    ],
    enabled: open,
    queryFn: ({ signal }) =>
      memoryProviderApi(client, scope).references(
        providerId,
        signal,
        page.cursor,
      ),
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
        <Loading variant="table" columns={2} rows={5} />
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
                tone: "primary",
                render: (item) => <Identifier value={item.agent_id} primary />,
              },
              {
                label: t("Revision"),
                tone: "muted",
                render: (item) => (
                  <>
                    <Identifier value={item.agent_revision_id} /> · v
                    {item.version} {item.is_current && t("Current")}
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
