import { useQuery } from "@tanstack/react-query";
import { GearSixIcon, PulseIcon } from "@phosphor-icons/react";

import type { TFunction } from "i18next";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { UserAvatar } from "../../layout/avatar";
import { data, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
  type ResourceColumn,
} from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { CopyableId } from "../../shared/identity";
import styles from "../../shared/shared.module.css";
import settings from "./settings.module.css";
import type { ProfileTarget } from "./profile";

type AuditEvent = Schema["AuditEvent"];

/** Words of a code the console has no label for: `memory.file.move` → "Memory file move". */
export function humanizeCode(code: string) {
  const words = code.replace(/[._]+/g, " ").trim();
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/** A readable action such as "Created agent"; new actions still read as words. */
export function auditAction(t: TFunction, action: string) {
  return t(`audit.${action}`, { defaultValue: humanizeCode(action) });
}

export function auditResource(t: TFunction, kind: string) {
  return t(`resource.${kind}`, { defaultValue: humanizeCode(kind) });
}

/**
 * Security activity for a workspace, an organization, or the reader's own
 * account. The service pages this collection and offers no server-side
 * filters, so the list stays unfiltered rather than filtering one page.
 */
export function Audit({ scope }: { scope: ProfileTarget }) {
  const client = useClient(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: [
      "audit",
      scope.kind,
      scope.kind === "personal" ? "me" : scope.id,
      page.cursor,
    ],
    queryFn: ({ signal }) => {
      const params = { query: { cursor: page.cursor, limit: 30 } };
      if (scope.kind === "personal")
        return client.http
          .GET("/api/v1/users/me/audit-events", { params, signal })
          .then(data);
      if (scope.kind === "workspace")
        return client.http
          .GET("/api/v1/workspaces/{workspace_id}/audit-events", {
            params: { ...params, path: { workspace_id: scope.id } },
            signal,
          })
          .then(data);
      return client.http
        .GET("/api/v1/organizations/{organization_id}/audit-events", {
          params: { ...params, path: { organization_id: scope.id } },
          signal,
        })
        .then(data);
    },
  });
  if (query.isPending) return <Loading variant="table" columns={3} rows={5} />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const items = query.data.items;
  if (!items.length && !page.previous)
    return (
      <Empty
        icon={<PulseIcon aria-hidden="true" />}
        title={t("No activity yet")}
        description={t("Security events will appear here as changes are made.")}
      />
    );
  const paged = !!page.previous || !!query.data.next_cursor;
  // The reader's own activity is all theirs, so it names no actor.
  const actor: ResourceColumn<AuditEvent>[] =
    scope.kind === "personal"
      ? []
      : [
          {
            label: t("Actor"),
            tone: "primary",
            render: (item) => <AuditActor item={item} />,
          },
        ];
  return (
    <div className={styles.stack}>
      <ResourceTable
        items={items}
        caption={t("Security activity")}
        columns={[
          ...actor,
          {
            label: t("Event"),
            tone: scope.kind === "personal" ? "primary" : "secondary",
            render: (item) => (
              <span
                className={settings.event}
                data-beside-actor={actor.length ? "" : undefined}
              >
                <span title={item.action}>{auditAction(t, item.action)}</span>
                <span className={settings.eventTarget}>
                  <span title={item.target_kind}>
                    {auditResource(t, item.target_kind)}
                  </span>
                  <CopyableId value={item.target_id} />
                </span>
              </span>
            ),
          },
          {
            label: t("When"),
            tone: "muted",
            render: (item) => <Timestamp value={item.occurred_at} relative />,
          },
        ]}
      />
      <CollectionFooter
        count={
          paged
            ? t("{{count}} events on this page", { count: items.length })
            : t("{{count}} events", { count: items.length })
        }
      >
        <Pagination page={page} next={query.data.next_cursor} />
      </CollectionFooter>
    </div>
  );
}

function AuditActor({ item }: { item: AuditEvent }) {
  const { t } = useTranslation();
  const user = item.actor?.kind === "user" ? item.actor : undefined;
  if (user)
    return (
      <ResourceIdentity
        icon={
          <UserAvatar
            name={user.name}
            id={user.id}
            url={user.image_url}
            className="size-8 rounded-[8px]"
          />
        }
        name={user.name}
        description={user.email ?? undefined}
        resourceId={user.id}
      />
    );
  return (
    <ResourceIdentity
      icon={<GearSixIcon size={15} aria-hidden="true" />}
      name={item.actor?.name ?? t("System")}
      description={item.actor_id ?? undefined}
      resourceId={item.actor_id ?? undefined}
    />
  );
}
