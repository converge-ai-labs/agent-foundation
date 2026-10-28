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
        Check environment readiness
      </Button>
      <ErrorNotice error={preflight.error} />
      {preflight.data && (
        <div role="status" className={styles.notice}>
          <strong>
            {preflight.data.ready ? "Environment ready" : "Action required"}
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
  const setup = useSetup();
  if (setup.isPending) return <p role="status">Discovering configuration…</p>;
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
        title="Setup & readiness"
        description="What this server needs before agents can run, and where to fix it."
        actions={<Button render={<Link to="/" />}>Open workspace</Button>}
      />
      {status?.diagnostic && (
        <div role="alert" className={styles.notice}>
          {status.diagnostic}
        </div>
      )}
      <SettingsSection variant="grouped" title="Configuration">
        <SettingsRow
          label="Default agent"
          description={
            status?.needed
              ? "Configuration is incomplete. Choose a default agent or repair the existing resource files."
              : (defaultAgent ?? "Not selected")
          }
        >
          <Button variant="outline" size="sm" render={<Link to="/settings" />}>
            Review defaults
          </Button>
        </SettingsRow>
        <SettingsRow
          label="Agent connections"
          description="Fix an agent whose model or account is missing."
        >
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/settings/agents" />}
          >
            Repair agent connection
          </Button>
        </SettingsRow>
        <SettingsRow
          label="Configuration files"
          description="Read and edit the YAML resources behind these settings."
        >
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/settings/resources" />}
          >
            Inspect configuration files
          </Button>
        </SettingsRow>
      </SettingsSection>
      <SettingsSection variant="grouped" title="Model accounts">
        <SettingsRow
          label="Accounts & API keys"
          description="Credentials are shared on this server. Account status does not verify model access."
        >
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/settings/accounts" />}
          >
            Manage accounts
          </Button>
        </SettingsRow>
      </SettingsSection>
      <SettingsSection variant="grouped" title="Execution environment">
        <SettingsRow
          label="Local runtime"
          description={
            status?.environment_profile === "environment-native"
              ? "Full Control runs as your host account. It is not a sandbox."
              : status?.environment_profile
          }
        >
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/settings/runtime" />}
          >
            Open local runtime
          </Button>
        </SettingsRow>
        <SettingsRow
          label="Execution defaults"
          description="The environment new chats start in."
        >
          <Button
            variant="outline"
            size="sm"
            render={<Link to="/settings/environments" />}
          >
            Review execution defaults
          </Button>
        </SettingsRow>
      </SettingsSection>
    </>
  );
}
