import { MenuItem } from "a13n-ui";

import { useQuery } from "@tanstack/react-query";
import { MonitorIcon, SignOutIcon } from "@phosphor-icons/react";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { allPages, data } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  ResourceIdentity,
  ResourceTable,
} from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Confirm } from "../../shared/dialogs";
import styles from "../../shared/shared.module.css";
import settings from "./settings.module.css";
import { Audit } from "./audit";
import { SettingsLayout } from "./layout";
import { Preferences, LanguageSettings } from "./preferences";
import { Profile } from "./profile";
import { Security } from "./security";

export function PersonalSettings() {
  return (
    <SettingsLayout
      scope="personal"
      content={{
        profile: <Profile target={{ kind: "personal" }} />,
        preferences: <Preferences />,
        language: <LanguageSettings />,
        security: <Security />,
        sessions: <BrowserSessions />,
        activity: <Audit scope={{ kind: "personal" }} />,
      }}
    />
  );
}

/**
 * Browsers holding an active session for this account. The service records
 * when a session began and when it expires, and which one is this browser; it
 * does not record the device or the place it signed in from, so neither is
 * shown. Sessions expire within hours, so the whole collection is read and
 * ordered here: this browser first, then the newest sign-in.
 */
function BrowserSessions() {
  const { t, i18n } = useTranslation(),
    client = useClient();
  const sessions = useQuery({
    queryKey: ["browser-sessions"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/users/me/login-sessions", {
            signal,
            params: { query: { cursor, limit: 100 } },
          })
          .then(data),
      ),
  });
  if (sessions.isPending)
    return <Loading variant="table" columns={2} rows={4} />;
  if (!sessions.data)
    return (
      <ErrorNotice
        error={sessions.error}
        retry={() => void sessions.refetch()}
      />
    );
  if (!sessions.data.length)
    return (
      <Empty
        icon={<MonitorIcon aria-hidden="true" />}
        title={t("No active sessions")}
        description={t("Signing in from a browser adds a session here.")}
      />
    );
  const items = [...sessions.data].sort(
    (a, b) =>
      Number(b.current) - Number(a.current) ||
      b.created_at.localeCompare(a.created_at),
  );
  const signedIn = new Intl.DateTimeFormat(i18n.resolvedLanguage, {
    dateStyle: "medium",
    timeStyle: "short",
  });
  return (
    <div className={styles.stack}>
      <ResourceTable
        items={items}
        caption={t("Browser sessions")}
        rowMenuLabel={t("Session actions")}
        rowMenu={(item) => (
          <Confirm
            subject={t("Session signed in {{time}}", {
              time: signedIn.format(new Date(item.created_at)),
            })}
            title={t("Revoke session")}
            description={
              item.current
                ? t("This signs you out of this browser.")
                : t("That browser will need to sign in again.")
            }
            triggerElement={
              <MenuItem closeOnClick={false} variant="destructive">
                <SignOutIcon size={14} />
                {t("Revoke")}
              </MenuItem>
            }
            danger
            action={() =>
              client.http.DELETE(
                "/api/v1/users/me/login-sessions/{session_id}",
                { params: { path: { session_id: item.id } } },
              )
            }
          />
        )}
        columns={[
          {
            label: t("Signed in"),
            tone: "primary",
            render: (item) => (
              <ResourceIdentity
                icon={<MonitorIcon size={15} aria-hidden="true" />}
                name={signedIn.format(new Date(item.created_at))}
                description={item.current ? t("This browser") : undefined}
                resourceId={item.id}
              />
            ),
          },
          {
            label: t("Expires"),
            tone: "muted",
            render: (item) => <Timestamp value={item.expires_at} relative />,
          },
        ]}
      />
      <CollectionFooter
        count={t("{{count}} sessions", { count: items.length })}
      />
      <p className={settings.footnote}>
        {t("Revoking a session signs that browser out immediately.")}
      </p>
    </div>
  );
}
