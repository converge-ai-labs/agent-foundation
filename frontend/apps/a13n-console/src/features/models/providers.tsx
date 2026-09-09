import { useQuery } from "@tanstack/react-query";
import { Badge, Button, ModalFrame } from "a13n-ui";
import { PlugZap, Plus } from "lucide-react";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useAccess } from "../../layout/workspace";
import { data } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty, ErrorNotice, Loading, StateBadge } from "../../shared/feedback";
import { PageActions } from "../../shared/page-actions";
import styles from "../../shared/shared.module.css";
import { modelApi, type ModelScope } from "./api";
import { Discovery, ProviderTest } from "./provider-discovery";
import { ProviderForm } from "./provider-form";

export function Providers({ scope }: { scope: ModelScope }) {
  const client = useClient(),
    { t } = useTranslation(),
    { can, organization, organizationAdmin } = useAccess(),
    page = useCursor();
  const api = modelApi(client, scope);
  const query = useQuery({
    queryKey: ["model-providers", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) => api.providers(signal, page.cursor),
  });
  const definitions = useQuery({
    queryKey: ["model-provider-types"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/model-provider-types", { signal }).then(data),
  });
  const manage =
    scope.kind === "organization" ? organizationAdmin : can("models.manage");
  return (
    <div className={styles.stack}>
      <PageActions>{manage && <ProviderEditor scope={scope} />}</PageActions>
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
                label: t("Provider"),
                render: (item) => (
                  <>
                    <PlugZap size={14} /> {item.name}
                    <small>{item.type}</small>
                  </>
                ),
              },
              {
                label: t("Scope"),
                render: (item) => (
                  <Badge variant={"secondary"}>
                    {t(item.workspace_id ? "Workspace" : "Organization")}
                  </Badge>
                ),
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
                align: "right",
                render: (item) => {
                  const owner: ModelScope = item.workspace_id
                    ? { kind: "workspace", id: item.workspace_id }
                    : { kind: "organization", id: organization.id };
                  return (
                    (item.workspace_id ? manage : organizationAdmin) && (
                      <div className={styles.actions}>
                        <ProviderEditor scope={owner} providerId={item.id} />
                        <ProviderTest scope={owner} providerId={item.id} />
                        {definitions.data?.items.find(
                          (definition) => definition.type === item.type,
                        )?.supports_model_discovery && (
                          <Discovery scope={scope} provider={item} />
                        )}
                      </div>
                    )
                  );
                },
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

export function ProviderEditor({
  scope,
  providerId,
}: {
  scope: ModelScope;
  providerId?: string;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    [open, setOpen] = useState(false),
    [generation, setGeneration] = useState(0);
  const api = modelApi(client, scope);
  const definitions = useQuery({
    queryKey: ["model-provider-types"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/model-provider-types", { signal }).then(data),
  });
  const resource = useQuery({
    queryKey: ["model-provider", scope.kind, scope.id, providerId],
    enabled: open && !!providerId,
    queryFn: ({ signal }) => api.provider(providerId!, signal),
  });
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button
          size={providerId ? "sm" : "default"}
          variant={providerId ? "outline" : "default"}
          type="button"
        >
          {!providerId && <Plus size={14} />}
          {t(providerId ? "Edit" : "Add provider")}
        </Button>
      }
      size={"md"}
      title={t(providerId ? "Edit provider" : "Add provider")}
      description={t(
        "Credentials are stored securely and never returned by the service.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {open &&
        (definitions.isPending || (providerId && resource.isPending) ? (
          <Loading />
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
              resource={providerId ? resource.data : undefined}
              definitions={definitions.data.items}
              close={() => setOpen(false)}
            />
          )
        ))}
    </ModalFrame>
  );
}
