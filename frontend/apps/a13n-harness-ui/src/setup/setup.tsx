import { useTranslation } from "../i18n";
import { SettingsRow, SettingsSection } from "../shell/settings";
import { useEffect } from "react";
import { Link } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useSetup, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, PageHeader } from "../shell/ui";
import { SetupWizard } from "./wizard";
import { readWizardDraft } from "./wizard-state";
import styles from "../shell/workbench.module.css";

export function Preflight({
  profile,
  projectPath,
}: {
  profile: "environment-native" | "environment-sandbox";
  projectPath?: string;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const preflight = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/environments/preflight", {
          body: { profile_id: profile, project_path: projectPath || "." },
        }),
      ),
  });
  useEffect(() => {
    preflight.reset();
  }, [profile, projectPath]);
  return (
    <div className={styles.stack}>
      <Button
        variant="outline"
        loading={preflight.isPending}
        onClick={() => preflight.mutate()}
      >
        {t("Check environments")}
      </Button>
      <ErrorNotice error={preflight.error} />
      {preflight.data && (
        <div role="status" className={styles.notice}>
          <strong>
            {preflight.data.ready
              ? t("Environment ready")
              : t("Action required")}
          </strong>
          <p>{preflight.data.message}</p>
          {preflight.data.instructions?.map((instruction) => (
            <p key={instruction}>{instruction}</p>
          ))}
        </div>
      )}
    </div>
  );
}

export function SetupPage() {
  const { t } = useTranslation();
  const setup = useSetup();
  if (setup.isPending)
    return <p role="status">{t("Discovering configuration…")}</p>;
  if (setup.error)
    return (
      <ErrorNotice error={setup.error} retry={() => void setup.refetch()} />
    );
  if (
    setup.data?.defaults &&
    setup.data.draft_scope &&
    (setup.data.fresh || readWizardDraft(setup.data.draft_scope))
  )
    return <SetupWizard key={setup.data.draft_scope} status={setup.data} />;
  const status = setup.data;
  const defaultAgent =
    status?.agents[status.default_agent ?? ""] || status?.default_agent;
  return (
    <>
      <PageHeader
        title={t("Setup & readiness")}
        description={t("Check what you need to start chatting.")}
        actions={
          <Button render={<Link to="/" />}>{t("Open workspace")}</Button>
        }
      />
      {status?.diagnostic && (
        <div role="alert" className={styles.notice}>
          {status.diagnostic}
        </div>
      )}
      <SettingsSection variant="grouped" title={t("Configuration")}>
        <SettingsRow
          label={t("Default agent")}
          description={
            status?.needed
              ? t(
                  "Setup is incomplete. Choose a default agent or fix its configuration.",
                )
              : (defaultAgent ?? t("Not selected"))
          }
        >
          <Button variant="outline" size="sm" render={<Link to="/settings" />}>
            {t("Review defaults")}
          </Button>
        </SettingsRow>
        <SettingsRow
          label={t("Agent connections")}
          description={t("Fix an agent whose model or account is missing.")}
        >
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/settings/agents" />}
          >
            {t("Fix connection")}
          </Button>
        </SettingsRow>
        <SettingsRow
          label={t("Configuration files")}
          description={t("Edit the configuration files behind these settings.")}
        >
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/settings/resources" />}
          >
            {t("Open configuration")}
          </Button>
        </SettingsRow>
      </SettingsSection>
      <SettingsSection variant="grouped" title={t("Model accounts")}>
        <SettingsRow
          label={t("Accounts & API keys")}
          description={t(
            "Accounts are shared on this server. Being signed in doesn't guarantee model access.",
          )}
        >
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/settings/accounts" />}
          >
            {t("Manage accounts")}
          </Button>
        </SettingsRow>
      </SettingsSection>
      <SettingsSection variant="grouped" title={t("Execution environment")}>
        <SettingsRow
          label={t("Local runtime")}
          description={
            status?.environment_profile === "environment-native"
              ? t(
                  "Full Control runs as your host account. It is not a sandbox.",
                )
              : status?.environment_profile
          }
        >
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/settings/runtime" />}
          >
            {t("Open local runtime")}
          </Button>
        </SettingsRow>
        <SettingsRow
          label={t("Execution defaults")}
          description={t("The environment new chats start in.")}
        >
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/settings/environments" />}
          >
            {t("Review execution")}
          </Button>
        </SettingsRow>
      </SettingsSection>
    </>
  );
}
