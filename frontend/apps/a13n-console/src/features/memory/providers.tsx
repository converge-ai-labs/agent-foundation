import { useQuery } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { useResourceRows } from "../../shared/dialogs";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { Identifier } from "../../shared/identity";
import styles from "../../shared/shared.module.css";
import { ProviderTable } from "../providers";
import { useMemoryProviderDefinitions } from "./availability";
import {
  AddMemoryProvider,
  MemoryProviderEditor,
  memoryCredentialState,
} from "./editor";
import { memoryProviderApi, type MemoryProviderScope } from "./providers-api";

export function MemoryProviders({ scope }: { scope: MemoryProviderScope }) {
  const client = useClient(),
    { can, organizationAdmin } = useAccess(),
    page = useCursor();
  const rows = useResourceRows<Schema["MemoryProvider"]>();
  const query = useQuery({
    queryKey: ["memory-providers", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      memoryProviderApi(client, scope).providers(signal, page.cursor),
  });
  const definitions = useMemoryProviderDefinitions(scope);
  const manage =
    scope.kind === "organization"
      ? organizationAdmin
      : can("memory_provider.manage");
  const add = manage ? <AddMemoryProvider scope={scope} /> : undefined;
  return (
    <>
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
            <MemoryReferences scope={scope} providerId={rows.selected.id} />
          }
        />
      )}
      <ProviderTable
        category="memory"
        items={query.data?.items}
        isPending={query.isPending}
        error={query.error}
        page={page}
        nextCursor={query.data?.next_cursor}
        action={add}
        onRowActivate={rows.activate}
        row={(item) => ({
          id: item.id,
          name: item.name,
          type: item.type,
          definition: definitions.data?.items.find(
            (definition) => definition.type === item.type,
          )?.display_name,
          workspaceId: item.workspace_id,
          credentials: memoryCredentialState(
            item,
            definitions.data?.items.find(
              (definition) => definition.type === item.type,
            ),
          ),
          state: item.enabled ? "enabled" : "disabled",
        })}
      />
    </>
  );
}

/** Which agent revisions still point at this backend. */
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
        <div className={styles.stack}>
          <ResourceTable
            caption={t("Agent references")}
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
        </div>
      )}
    </ModalFrame>
  );
}
