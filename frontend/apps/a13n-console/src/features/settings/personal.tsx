import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Input, SettingsSection } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useAuth, useClient } from "../../auth/context";
import { data } from "../../shared/api";
import {
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Confirm, FormActions } from "../../shared/form";
import { SettingsLayout } from "./layout";
import { Preferences } from "./preferences";
import { Profile } from "./profile";
import { Audit } from "./audit";
import styles from "../../shared/shared.module.css";

export function PersonalSettings() {
  const { t } = useTranslation();
  const auth = useAuth();
  return (
    <SettingsLayout
      scope="personal"
      name={auth.data!.user.value.name}
      items={[
        {
          value: "profile",
          label: t("Profile"),
          content: <Profile target={{ kind: "personal" }} />,
        },
        {
          value: "preferences",
          label: t("Preferences"),
          content: <Preferences />,
        },
        { value: "security", label: t("Security"), content: <Security /> },
        {
          value: "sessions",
          label: t("Sessions"),
          content: <BrowserSessions />,
        },
        {
          value: "activity",
          label: t("Activity"),
          content: <Audit scope={{ kind: "personal" }} />,
        },
      ]}
    />
  );
}
function Security() {
  const { t } = useTranslation(),
    auth = useAuth(),
    client = useClient(),
    navigate = useNavigate();
  const [email, setEmail] = useState(auth.data!.user.value.email),
    [emailPassword, setEmailPassword] = useState(""),
    [currentPassword, setCurrentPassword] = useState(""),
    [password, setPassword] = useState("");
  const config = useQuery({
    queryKey: ["auth-configuration"],
    queryFn: ({ signal }) =>
      client.http.GET("/api/v1/auth/configuration", { signal }).then(data),
  });
  const changeEmail = useMutation({
    mutationFn: () =>
      client.http.POST("/api/v1/users/me/email-change", {
        body: { email, current_password: emailPassword },
      }),
    onSuccess: () => setEmailPassword(""),
  });
  const changePassword = useMutation({
    mutationFn: () =>
      client.http.POST("/api/v1/users/me/password", {
        body: { current_password: currentPassword, password },
      }),
    onSuccess: () => {
      setCurrentPassword("");
      setPassword("");
      void auth.refresh();
      navigate("/login");
    },
  });
  return (
    <div className={styles.stack}>
      <SettingsSection
        variant="plain"
        title={t("Email address")}
        description={t(
          "Verify a new address before it becomes your sign-in email.",
        )}
      >
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            changeEmail.mutate();
          }}
        >
          <Input
            label={t("Email address")}
            type="email"
            value={email}
            onChange={(event) => setEmail(event.target.value)}
            required
          />
          <Input
            label={t("Current password")}
            type="password"
            autoComplete="current-password"
            value={emailPassword}
            onChange={(event) => setEmailPassword(event.target.value)}
            required
          />
          {config.data?.email_delivery === false ? (
            <p className={styles.muted}>
              {t(
                "Email delivery is not configured. Contact your organization administrator.",
              )}
            </p>
          ) : (
            <FormActions
              pending={changeEmail.isPending}
              label={t("Send verification email")}
            />
          )}
          <ErrorNotice error={changeEmail.error} />
          {changeEmail.isSuccess && (
            <p role="status">
              {t("Check your new inbox for the verification link.")}
            </p>
          )}
        </form>
      </SettingsSection>
      <SettingsSection
        variant="plain"
        title={t("Change password")}
        description={t(
          "Changing your password signs out all browser sessions. API keys remain active.",
        )}
      >
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            changePassword.mutate();
          }}
        >
          <Input
            label={t("Current password")}
            type="password"
            autoComplete="current-password"
            value={currentPassword}
            onChange={(event) => setCurrentPassword(event.target.value)}
            required
          />
          <Input
            label={t("New password")}
            type="password"
            autoComplete="new-password"
            value={password}
            onChange={(event) => setPassword(event.target.value)}
            required
            minLength={15}
            maxLength={128}
            hint={t("Use at least 15 characters.")}
          />
          <ErrorNotice error={changePassword.error} />
          <FormActions
            pending={changePassword.isPending}
            label={t("Change password")}
          />
        </form>
      </SettingsSection>
    </div>
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
  if (sessions.isPending) return <Loading />;
  if (!sessions.data) return <ErrorNotice error={sessions.error} />;
  return (
    <>
      <div className={styles.tableWrap}>
        <table className={styles.table}>
          <thead>
            <tr>
              <th>{t("Session")}</th>
              <th>{t("Created")}</th>
              <th>{t("Expires")}</th>
              <th>{t("Status")}</th>
              <th>{t("Actions")}</th>
            </tr>
          </thead>
          <tbody>
            {sessions.data.items.map((item) => (
              <tr key={item.id}>
                <td>
                  <code>{item.id}</code>
                </td>
                <td>
                  <Timestamp value={item.created_at} />
                </td>
                <td>
                  <Timestamp value={item.expires_at} />
                </td>
                <td>
                  <StateBadge state={item.revoked_at ? "revoked" : "active"} />
                </td>
                <td>
                  {!item.revoked_at && (
                    <Confirm
                      title={t("Revoke session")}
                      description={t(
                        "This browser will need to sign in again.",
                      )}
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
                  )}
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
        {sessions.data.next_cursor && (
          <Button
            onClick={() => setCursor(sessions.data.next_cursor ?? undefined)}
          >
            {t("Next page")}
          </Button>
        )}
      </div>
    </>
  );
}
