import { MenuItem } from "a13n-ui";

import { useQuery, useQueryClient } from "@tanstack/react-query";
import { MonitorIcon, SignOutIcon } from "@phosphor-icons/react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { Confirm } from "../../shared/dialogs";
import styles from "../../shared/shared.module.css";
import settings from "./settings.module.css";
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

/**
 * Browsers holding a session for this account. The service records when a
 * session began and when it expires; it does not record the device or the
 * place it signed in from, so neither is shown.
 */
function BrowserSessions() {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    page = useCursor();
  const sessions = useQuery({
    queryKey: ["browser-sessions", page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/users/me/auth-sessions", {
          signal,
          params: { query: { cursor: page.cursor, limit: 30 } },
        })
        .then(data),
  });
  if (sessions.isPending)
    return <Loading variant="table" columns={4} rows={4} />;
  if (!sessions.data)
    return (
      <ErrorNotice
        error={sessions.error}
        retry={() => void sessions.refetch()}
      />
    );
  const items = sessions.data.items;
  if (!items.length && !page.previous)
    return (
      <Empty
        icon={<MonitorIcon aria-hidden="true" />}
        title={t("No active sessions")}
        description={t("Signing in from a browser adds a session here.")}
      />
    );
  return (
    <div className={styles.stack}>
      <ResourceTable
        items={items}
        caption={t("Browser sessions")}
        rowMenuLabel={t("Session actions")}
        rowMenu={(item) =>
          item.revoked_at ? null : (
            <Confirm
              subject={item.id}
              title={t("Revoke session")}
              description={t("This browser will need to sign in again.")}
              triggerElement={
                <MenuItem closeOnClick={false} variant="destructive">
                  <SignOutIcon size={14} />
                  {t("Revoke")}
                </MenuItem>
              }
              danger
              action={async () => {
                await client.http.DELETE(
                  "/api/v1/users/me/auth-sessions/{session_id}",
                  { params: { path: { session_id: item.id } } },
                );
                await cache.invalidateQueries({ queryKey: ["identity"] });
              }}
            />
          )
        }
        columns={[
          {
            label: t("Session"),
            tone: "primary",
            render: (item) => (
              <ResourceIdentity
                icon={<MonitorIcon size={15} aria-hidden="true" />}
                name={t("Browser session")}
                resourceId={item.id}
              />
            ),
          },
          {
            label: t("Signed in"),
            tone: "muted",
            render: (item) => <Timestamp value={item.created_at} relative />,
          },
          {
            label: t("Expires"),
            tone: "muted",
            render: (item) => <Timestamp value={item.expires_at} />,
          },
          {
            label: t("Status"),
            render: (item) => (
              <StatePill state={item.revoked_at ? "revoked" : "active"} />
            ),
          },
        ]}
      />
      <CollectionFooter
        count={t("{{count}} sessions", { count: items.length })}
      >
        <Pagination page={page} next={sessions.data.next_cursor} />
      </CollectionFooter>
      <p className={settings.note}>
        {t("Revoking a session signs that browser out immediately.")}
      </p>
    </div>
  );
}
