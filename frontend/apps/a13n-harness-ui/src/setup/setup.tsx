import { useEffect } from "react";
import { Link } from "react-router";
import { useMutation } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useSetup, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, PageHeader, Panel } from "../shell/ui";
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
  return (
    <>
      <PageHeader
        title="Setup & readiness"
        description="Keep your existing configuration. Repair only what needs attention; no resources are reinitialized here."
      />
      {status?.diagnostic && (
        <div role="alert" className={styles.notice}>
          {status.diagnostic}
        </div>
      )}
      <Panel title="Configuration">
        <p>
          {status?.needed
            ? "Your configuration is incomplete. Choose a default Agent or repair the existing resource files."
            : "Your default Agent is configured. Account availability and execution readiness are separate checks."}
        </p>
        <p>
          Default Agent:{" "}
          {status?.agents[status.default_agent ?? ""] ||
            status?.default_agent ||
            "Not selected"}
        </p>
        <div className={styles.actions}>
          <Button variant="outline" render={<Link to="/settings" />}>
            Review defaults
          </Button>
          <Button variant="outline" render={<Link to="/settings/agents" />}>
            Repair agent connection
          </Button>
          <Button variant="outline" render={<Link to="/settings/resources" />}>
            Inspect configuration files
          </Button>
        </div>
      </Panel>
      <Panel title="Model accounts">
        <p>
          Connected credentials are shared by this server. Checking their status
          does not send a model request or verify model entitlement.
        </p>
        <Button variant="outline" render={<Link to="/settings/accounts" />}>
          Manage accounts & API keys
        </Button>
      </Panel>
      <Panel title="Execution environment">
        <p>
          {status?.environment_profile === "environment-native"
            ? "Full Control runs as your Host account; it is not a sandbox."
            : status?.environment_profile}
        </p>
        <Button variant="outline" render={<Link to="/settings/environments" />}>
          Review environment readiness
        </Button>
      </Panel>
      <Button render={<Link to="/" />}>Open workbench</Button>
    </>
  );
}
