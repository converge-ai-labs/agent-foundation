import { Button } from "a13n-ui";
import { ResourceTable } from "../../shared/collection";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { CopyableId } from "../../shared/copy";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Confirm } from "../../shared/form";
import styles from "../../shared/shared.module.css";
import { Audit } from "./audit";
import { SettingsLayout } from "./layout";
import { Preferences } from "./preferences";
import { Profile } from "./profile";
import { Security } from "./security";

export function PersonalSettings() {
  return (
    <SettingsLayout
      scope="personal"
      content={{
        profile: <Profile target={{ kind: "personal" }} />,
        preferences: <Preferences />,
        security: <Security />,
        sessions: <BrowserSessions />,
        activity: <Audit scope={{ kind: "personal" }} />,
      }}
    />
  );
}
function BrowserSessions() {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [cursor, setCursor] = useState<string>();
  const sessions = useQuery({
    queryKey: ["browser-sessions", cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/users/me/auth-sessions", {
          signal,
          params: { query: { cursor, limit: 30 } },
        })
        .then(data),
  });
  if (sessions.isPending) return <Loading variant="table" columns={5} />;
  if (!sessions.data) return <ErrorNotice error={sessions.error} />;
  return (
    <>
      <div className={styles.tableWrap}>
        <ResourceTable
          caption={t("Browser sessions")}
          items={sessions.data.items}
          columns={[
            {
              label: t("Session"),
              tone: "primary",
              render: (item) => <CopyableId value={item.id} />,
            },
            {
              label: t("Created"),
              tone: "muted",
              render: (item) => <Timestamp value={item.created_at} />,
            },
            {
              label: t("Expires"),
              tone: "muted",
              render: (item) => <Timestamp value={item.expires_at} />,
            },
            {
              label: t("Status"),
              render: (item) => (
                <StateBadge state={item.revoked_at ? "revoked" : "active"} />
              ),
            },
            {
              label: t("Actions"),
              align: "right",
              render: (item) =>
                !item.revoked_at && (
                  <Confirm
                    subject={item.id}
                    triggerVariant="ghost"
                    title={t("Revoke session")}
                    description={t("This browser will need to sign in again.")}
                    trigger={t("Revoke")}
                    danger
                    action={async () => {
                      await client.http.DELETE(
                        "/api/v1/users/me/auth-sessions/{session_id}",
                        { params: { path: { session_id: item.id } } },
                      );
                      await cache.invalidateQueries({
                        queryKey: ["identity"],
                      });
                    }}
                  />
                ),
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
        {sessions.data.next_cursor && (
          <Button
            variant="outline"
            onClick={() => setCursor(sessions.data.next_cursor ?? undefined)}
            type="button"
          >
            {t("Next page")}
          </Button>
        )}
      </div>
    </>
  );
}
