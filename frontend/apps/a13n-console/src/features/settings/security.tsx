import {
  Button,
  FormField,
  Input,
  ModalFrame,
  SettingsSection,
  SettingsRow,
} from "a13n-ui";

import { useMutation, useQuery } from "@tanstack/react-query";
import { useId, useRef, useState } from "react";

import { InfoIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useNavigate } from "react-router";
import { useAuth, useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import styles from "./security.module.css";
import settings from "./settings.module.css";

export function Security() {
  const { t } = useTranslation();
  const auth = useAuth(),
    client = useClient(),
    navigate = useNavigate();
  const id = useId();
  const emailTrigger = useRef<HTMLButtonElement>(null);
  const [editingEmail, setEditingEmail] = useState(false);
  const [email, setEmail] = useState(auth.data!.user.value.email);
  const [emailPassword, setEmailPassword] = useState("");
  const [passwordOpen, setPasswordOpen] = useState(false);
  const [currentPassword, setCurrentPassword] = useState("");
  const [password, setPassword] = useState("");
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
    onSuccess: () => {
      setEmailPassword("");
      setEditingEmail(false);
    },
  });
  const changePassword = useMutation({
    mutationFn: () =>
      client.http.POST("/api/v1/users/me/password", {
        body: { current_password: currentPassword, password },
      }),
    onSuccess: () => {
      setCurrentPassword("");
      setPassword("");
      setPasswordOpen(false);
      void auth.refresh();
      navigate("/login");
    },
  });
  const togglePassword = (open: boolean) => {
    if (changePassword.isPending) return;
    setPasswordOpen(open);
    setCurrentPassword("");
    setPassword("");
    changePassword.reset();
  };
  return (
    <div className={settings.sections}>
      <SettingsSection
        title={t("Email address")}
        description={t(
          "Verify a new address before it becomes your sign-in email.",
        )}
      >
        <SettingsRow label={auth.data!.user.value.email}>
          <Button
            ref={emailTrigger}
            aria-expanded={editingEmail}
            aria-controls={`${id}-email-form`}
            variant="outline"
            disabled={
              config.isPending ||
              config.isError ||
              config.data?.email_delivery === false ||
              changeEmail.isPending
            }
            onClick={() => {
              setEditingEmail(!editingEmail);
              changeEmail.reset();
            }}
            type="button"
          >
            {t("Change email")}
          </Button>
        </SettingsRow>
        {config.data?.email_delivery === false && (
          <div className={styles.note}>
            <InfoIcon size={14} aria-hidden="true" />
            <p>
              {t(
                "Email delivery is not configured. Contact your organization administrator.",
              )}
            </p>
          </div>
        )}
        {config.isError && (
          <div className={styles.notice}>
            <ErrorNotice
              error={config.error}
              retry={() => void config.refetch()}
            />
          </div>
        )}
        {editingEmail && (
          <form
            id={`${id}-email-form`}
            onSubmit={(event) => {
              event.preventDefault();
              changeEmail.mutate();
            }}
          >
            <div className={styles.fields}>
              <SettingsRow
                label={t("New email address")}
                controlId={`${id}-email`}
              >
                <FormField
                  className="min-w-0 w-60 max-w-full"
                  label={t("New email address")}
                  hideLabel={true}
                >
                  <Input
                    required={true}
                    id={`${id}-email`}
                    type="email"
                    autoComplete="email"
                    value={email}
                    onChange={(event) => setEmail(event.target.value)}
                  />
                </FormField>
              </SettingsRow>
              <SettingsRow
                label={t("Current password")}
                controlId={`${id}-email-password`}
              >
                <FormField
                  className="min-w-0 w-60 max-w-full"
                  label={t("Current password")}
                  hideLabel={true}
                >
                  <Input
                    required={true}
                    id={`${id}-email-password`}
                    type="password"
                    autoComplete="current-password"
                    value={emailPassword}
                    onChange={(event) => setEmailPassword(event.target.value)}
                  />
                </FormField>
              </SettingsRow>
            </div>
            {changeEmail.error && (
              <div className={styles.notice}>
                <ErrorNotice error={changeEmail.error} />
              </div>
            )}
            <footer className={styles.actions}>
              <Button
                variant="outline"
                disabled={changeEmail.isPending}
                onClick={() => {
                  setEditingEmail(false);
                  setEmailPassword("");
                  emailTrigger.current?.focus();
                }}
                type="button"
              >
                {t("Cancel")}
              </Button>
              <Button
                type="submit"
                variant="default"
                loading={changeEmail.isPending}
              >
                {t("Send verification email")}
              </Button>
            </footer>
          </form>
        )}
        {changeEmail.isSuccess && (
          <p role="status" className={styles.success}>
            {t("Check your new inbox for the verification link.")}
          </p>
        )}
      </SettingsSection>
      <SettingsSection title={t("Password")}>
        <SettingsRow label={t("Use a strong, unique password.")}>
          <ModalFrame
            onOpenChange={togglePassword}
            trigger={
              <Button variant="outline" type="button">
                {t("Change password")}
              </Button>
            }
            size={"md"}
            title={t("Change password")}
            description={t(
              "Changing your password signs out all browser sessions. API keys remain active.",
            )}
            closeLabel={t("Close")}
            open={passwordOpen}
          >
            <form
              className={styles.passwordForm}
              onSubmit={(event) => {
                event.preventDefault();
                changePassword.mutate();
              }}
            >
              <FormField
                className="min-w-0 w-full"
                label={t("Current password")}
              >
                <Input
                  required={true}
                  type="password"
                  autoComplete="current-password"
                  value={currentPassword}
                  onChange={(event) => setCurrentPassword(event.target.value)}
                />
              </FormField>
              <FormField
                className="min-w-0 w-full"
                label={t("New password")}
                description={t("Use at least 15 characters.")}
              >
                <Input
                  required={true}
                  type="password"
                  autoComplete="new-password"
                  value={password}
                  onChange={(event) => setPassword(event.target.value)}
                  minLength={15}
                  maxLength={128}
                />
              </FormField>
              <ErrorNotice error={changePassword.error} />
              <FormActions
                pending={changePassword.isPending}
                label={t("Change password")}
                onCancel={() => togglePassword(false)}
              />
            </form>
          </ModalFrame>
        </SettingsRow>
      </SettingsSection>
    </div>
  );
}
