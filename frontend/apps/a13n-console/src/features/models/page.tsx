import { Boxes } from "lucide-react";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Badge, Input, Tabs } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace, useAccess } from "../../layout/workspace";
import {
  Empty,
  ErrorNotice,
  Loading,
  Page,
  StateBadge,
} from "../../shared/feedback";
import {
  Pagination,
  Table,
  ResourceIdentity,
  useCursor,
} from "../../shared/collection";
import { modelApi, type ModelScope } from "./api";
import { Providers } from "./providers";
import { ModelEditor, ModelTest } from "./model-editor";
import styles from "../../shared/shared.module.css";

export function ModelsPage() {
  const { t } = useTranslation(),
    { workspace } = useWorkspace();
  return (
    <Page
      title={t("Models")}
      description={t(
        "Connect providers and choose the models your agents can use.",
      )}
    >
      <Models scope={{ kind: "workspace", id: workspace.id }} />
    </Page>
  );
}
export function Models({ scope }: { scope: ModelScope }) {
  const { t } = useTranslation();
  return (
    <Tabs
      label={t("Model resources")}
      defaultValue="models"
      items={[
        {
          value: "models",
          label: t("Models"),
          content: <ModelList scope={scope} />,
        },
        {
          value: "providers",
          label: t("Providers"),
          content: <Providers scope={scope} />,
        },
      ]}
    />
  );
}
function ModelList({ scope }: { scope: ModelScope }) {
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
  const manage =
    scope.kind === "organization" ? organizationAdmin : can("models.manage");
  return (
    <div className={styles.stack}>
      <div className={styles.toolbar}>
        <Input
          label={t("Search models")}
          placeholder={t("Name or model key…")}
          value={search}
          onChange={(event) => {
            setSearch(event.target.value);
            page.reset();
          }}
        />
        {manage && <ModelEditor scope={scope} />}
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              {
                label: t("Model"),
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    description={item.key}
                    icon={<Boxes size={17} />}
                  />
                ),
              },
              {
                label: t("Upstream model"),
                render: (item) => (
                  <>
                    {item.upstream_model}
                    <small>{item.model_api}</small>
                  </>
                ),
              },
              {
                label: t("Scope"),
                render: (item) => (
                  <Badge>
                    {t(item.workspace_id ? "Workspace" : "Organization")}
                  </Badge>
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
                render: (item) => {
                  const owner: ModelScope = item.workspace_id
                    ? { kind: "workspace", id: item.workspace_id }
                    : { kind: "organization", id: organization.id };
                  return (
                    (item.workspace_id ? manage : organizationAdmin) && (
                      <div className={styles.actions}>
                        <ModelEditor scope={owner} modelId={item.id} />
                        <ModelTest scope={owner} modelId={item.id} />
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
          title={t("No models yet")}
          description={t(
            "Add a provider, then save a model alias for your agents.",
          )}
        />
      )}
    </div>
  );
}
