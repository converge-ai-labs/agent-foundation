import { useResourceEditorState, useResourceRows } from "../../shared/dialogs";
import { ResourceIdentity } from "../../shared/collection";
import { ScopeBadge } from "../../shared/identity";
import { useQuery } from "@tanstack/react-query";
import { ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty } from "../../shared/collection";
import { ErrorNotice, Loading, StatePill } from "../../shared/feedback";
import { PageActions } from "../../shared/page";
import styles from "../../shared/shared.module.css";
import { modelApi, type ModelScope } from "./api";
import { requiresProviderCredential } from "./provider-credentials";
import { ProviderForm } from "./provider-form";
import { ProviderIcon } from "../../shared/identity";
import { useModelProviderDefinitions } from "./provider-definitions";
import { AddProvider } from "./add-provider";

export function Providers({ scope }: { scope: ModelScope }) {
  const client = useClient(),
    { t } = useTranslation(),
    { can, organization, organizationAdmin } = useAccess(),
    page = useCursor();
  const rows = useResourceRows<Schema["ModelProvider"]>();
  const { selected } = rows;
  const api = modelApi(client, scope);
  const query = useQuery({
    queryKey: ["model-providers", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) => api.providers(signal, page.cursor),
  });
  const definitions = useModelProviderDefinitions();
  const manage =
    scope.kind === "organization" ? organizationAdmin : can("models.manage");
  return (
    <div className={styles.stack}>
      <PageActions>{manage && <AddProvider scope={scope} />}</PageActions>
      {selected && (
        <EditProviderDialog
          key={selected.id}
          scope={
            selected.workspace_id
              ? { kind: "workspace", id: selected.workspace_id }
              : { kind: "organization", id: organization.id }
          }
          providerId={selected.id}
          {...rows.control}
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
            canActivateRow={(item) =>
              item.workspace_id ? manage : organizationAdmin
            }
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
                    !requiresProviderCredential(
                      item.type,
                      item.configuration,
                      definitions.data?.items.find(
                        (definition) => definition.type === item.type,
                      ),
                    )
                      ? "No credentials required"
                      : item.credential_configured
                        ? "Configured"
                        : "Not configured",
                  ),
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StatePill state={item.enabled ? "enabled" : "disabled"} />
                ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        <Empty
          title={t("Connect your first provider")}
          description={t(
            "Add an account or endpoint, then choose the models your agents can use.",
          )}
        />
      )}
    </div>
  );
}

/** Editing an existing provider; creation goes through {@link AddProvider}. */
export function EditProviderDialog({
  scope,
  providerId,
  controlledOpen,
  onClose,
  finalFocus,
}: {
  scope: ModelScope;
  providerId: string;
  controlledOpen?: boolean;
  onClose?: () => void;
  finalFocus?: React.RefObject<HTMLElement | null>;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [generation, setGeneration] = useState(0);
  const { open, setOpen, modalProps } = useResourceEditorState({
    controlledOpen,
    onClose,
    finalFocus,
  });

  const api = modelApi(client, scope);
  const definitions = useModelProviderDefinitions();
  const resource = useQuery({
    queryKey: ["model-provider", scope.kind, scope.id, providerId],
    enabled: open,
    queryFn: ({ signal }) => api.provider(providerId, signal),
  });
  return (
    <ModalFrame
      {...modalProps}
      size="lg"
      title={t("Edit provider")}
      closeLabel={t("Close")}
    >
      {open &&
        (definitions.isPending || resource.isPending ? (
          <Loading variant="form" rows={4} />
        ) : (!definitions.data && definitions.error) ||
          (!resource.data && resource.error) ? (
          <ErrorNotice error={definitions.error ?? resource.error} />
        ) : (
          definitions.data && (
            <ProviderForm
              key={generation}
              reload={async () => {
                const result = await resource.refetch();
                if (!result.error) setGeneration((value) => value + 1);
              }}
              scope={scope}
              resource={resource.data}
              definitions={definitions.data.items}
              close={() => setOpen(false)}
            />
          )
        ))}
    </ModalFrame>
  );
}
