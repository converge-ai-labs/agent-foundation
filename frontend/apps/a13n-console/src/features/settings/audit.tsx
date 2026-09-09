import { Button } from "a13n-ui";
import { ResourceTable } from "../../shared/collection";

import { useQuery } from "@tanstack/react-query";
import { useState } from "react";
import { CopyableId } from "../../shared/copy";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { Empty, ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import styles from "../../shared/shared.module.css";
import type { ProfileTarget } from "./profile";
export function Audit({ scope }: { scope: ProfileTarget }) {
  const client = useClient(),
    { t } = useTranslation(),
    [cursor, setCursor] = useState<string>();
  const query = useQuery({
    queryKey: [
      "audit",
      scope.kind,
      scope.kind === "personal" ? "me" : scope.id,
      cursor,
    ],
    queryFn: ({ signal }) => {
      const params = { query: { cursor, limit: 30 } };
      if (scope.kind === "personal")
        return client.http
          .GET("/api/v1/users/me/security-activity", { params, signal })
          .then(data);
      if (scope.kind === "workspace")
        return client.http
          .GET("/api/v1/workspaces/{workspace}/security-audit-events", {
            params: { ...params, path: { workspace: scope.id } },
            signal,
          })
          .then(data);
      return client.http
        .GET("/api/v1/organizations/{organization}/security-audit-events", {
          params: { ...params, path: { organization: scope.id } },
          signal,
        })
        .then(data);
    },
  });
  if (query.isPending) return <Loading />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  if (!query.data.items.length)
    return (
      <Empty
        title={t("No activity yet")}
        description={t("Security events will appear here as changes are made.")}
      />
    );
  return (
    <>
      <div className={styles.tableWrap}>
        <ResourceTable
          caption={t("Activity")}
          items={query.data.items}
          columns={[
            {
              label: t("Action"),
              render: (item) => (
                <>
                  {item.action}
                  <small>
                    <CopyableId value={item.id} />
                  </small>
                </>
              ),
            },
            {
              label: t("Actor"),
              render: (item) =>
                item.actor_id ? (
                  <CopyableId value={item.actor_id} />
                ) : (
                  t("System")
                ),
            },
            {
              label: t("Resource"),
              render: (item) => (
                <>
                  {item.resource_type}
                  <small>
                    {item.resource_id && (
                      <CopyableId value={item.resource_id} />
                    )}
                  </small>
                </>
              ),
            },
            {
              label: t("Time"),
              render: (item) => <Timestamp value={item.occurred_at} />,
            },
          ]}
        />
      </div>
      <div className={styles.pagination}>
        {cursor && (
          <Button
            variant="outline"
            onClick={() => setCursor(undefined)}
            type="button"
          >
            {t("First page")}
          </Button>
        )}
        {query.data.next_cursor && (
          <Button
            variant="outline"
            onClick={() => setCursor(query.data.next_cursor ?? undefined)}
            type="button"
          >
            {t("Next page")}
          </Button>
        )}
      </div>
    </>
  );
}
