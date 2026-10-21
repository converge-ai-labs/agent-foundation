import { PlusIcon, WebhooksLogoIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../../shared/collection";
import { ErrorNotice, Loading, StatePill } from "../../../shared/feedback";
import { PageActions } from "../../../shared/page";
import shared from "../../../shared/shared.module.css";
import { webhookQueries } from "./api";
import settings from "../settings.module.css";
import { WebhookEditor } from "./editor";

export function Webhooks() {
  const client = useClient(),
    { workspace, can, basePath } = useWorkspace(),
    { t } = useTranslation(),
    navigate = useNavigate();
  const page = useCursor();
  const [creating, setCreating] = useState(false);
  const opener = useRef<HTMLButtonElement>(null);
  const headerAction = useRef<HTMLButtonElement>(null);
  const query = useQuery({
    ...webhookQueries(client, workspace.id).list(page.cursor),
    enabled: can("admin"),
  });
  if (!can("admin"))
    return (
      <Empty
        title={t("Access unavailable")}
        description={t("Workspace administrator access is required.")}
      />
    );
  const create = (header = false) => (
    <Button
      ref={header ? headerAction : undefined}
      type="button"
      onClick={(event) => {
        opener.current = event.currentTarget;
        setCreating(true);
      }}
    >
      <PlusIcon size={14} aria-hidden="true" />
      {t("Add Webhook")}
    </Button>
  );
  return (
    <div className={shared.stack}>
      <WebhookEditor
        open={creating}
        onOpenChange={setCreating}
        trigger={false}
        finalFocus={() =>
          opener.current?.isConnected ? opener.current : headerAction.current
        }
      />
      <PageActions>{create(true)}</PageActions>
      {query.isPending ? (
        <Loading variant="table" columns={4} rows={4} />
      ) : query.error ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : query.data.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            caption={t("Webhooks")}
            onRowActivate={(item) =>
              navigate(`${basePath}/settings/webhooks/${item.id}`)
            }
            columns={[
              {
                label: t("Name / endpoint"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    name={item.name}
                    description={item.url}
                    icon={<WebhooksLogoIcon size={16} aria-hidden="true" />}
                    to={`${basePath}/settings/webhooks/${item.id}`}
                    resourceId={item.id}
                  />
                ),
              },
              {
                label: t("Events"),
                render: (item) =>
                  t("{{count}} event types", { count: item.kinds.length }),
              },
              {
                label: t("Scope"),
                render: (item) =>
                  [
                    item.filter.agent_id && t("Agent"),
                    item.filter.session_id && t("Session"),
                    item.filter.thread_id && t("Thread"),
                    item.filter.schedule_id && t("Schedule"),
                  ]
                    .filter(Boolean)
                    .join(" · ") || t("Entire workspace"),
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StatePill state={item.enabled ? "enabled" : "disabled"} />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={
              page.previous || query.data.next_cursor
                ? t("{{count}} Webhooks on this page", {
                    count: query.data.items.length,
                  })
                : t("{{count}} Webhooks", { count: query.data.items.length })
            }
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
          <p className={settings.footnote}>
            {t(
              "Disabling stops new notifications. Queued deliveries continue.",
            )}
          </p>
        </>
      ) : (
        <Empty
          title={t("Receive run lifecycle notifications")}
          description={t(
            "Add an endpoint and choose the events your application needs.",
          )}
          icon={<WebhooksLogoIcon aria-hidden="true" />}
          action={create()}
        />
      )}
    </div>
  );
}
