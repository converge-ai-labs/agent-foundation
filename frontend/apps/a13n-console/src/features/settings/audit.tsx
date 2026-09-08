import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { Empty, ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import type { ProfileTarget } from "./profile";
import styles from "../../shared/shared.module.css";
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
          .GET("/api/v1/workspaces/{workspace_id}/security-audit-events", {
            params: { ...params, path: { workspace_id: scope.id } },
            signal,
          })
          .then(data);
      return client.http
        .GET("/api/v1/organizations/{organization_id}/security-audit-events", {
          params: { ...params, path: { organization_id: scope.id } },
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
        <table className={styles.table}>
          <thead>
            <tr>
              <th>{t("Action")}</th>
              <th>{t("Actor")}</th>
              <th>{t("Resource")}</th>
              <th>{t("Time")}</th>
            </tr>
          </thead>
          <tbody>
            {query.data.items.map((item) => (
              <tr key={item.id}>
                <td>
                  {item.action}
                  <small>{item.id}</small>
                </td>
                <td>{item.actor_id ?? t("System")}</td>
                <td>
                  {item.resource_type}
                  <small>{item.resource_id}</small>
                </td>
                <td>
                  <Timestamp value={item.occurred_at} />
                </td>
              </tr>
            ))}
          </tbody>
        </table>
      </div>
      <div className={styles.pagination}>
        {cursor && (
          <Button onClick={() => setCursor(undefined)}>
            {t("First page")}
          </Button>
        )}
        {query.data.next_cursor && (
          <Button
            onClick={() => setCursor(query.data.next_cursor ?? undefined)}
          >
            {t("Next page")}
          </Button>
        )}
      </div>
    </>
  );
}
