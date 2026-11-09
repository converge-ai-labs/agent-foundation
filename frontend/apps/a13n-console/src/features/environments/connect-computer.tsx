import { LaptopIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input, ModalFrame } from "a13n-ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Timestamp } from "../../shared/feedback";
import { CodeBlock } from "../../shared/forms";
import { EnvironmentStatus } from "./instance-status";

const guide = "https://a13n.dev/docs/a13n-envd/installation/";

export function pairingCommand(pairing: Schema["PairingCreated"]) {
  const quote = (value: string) => `'${value.replaceAll("'", "'\\''")}'`;
  return `a13n-envd connect-service ${quote(pairing.service_url)} --workspace-id ${quote(pairing.workspace_id)} --host ${quote(pairing.host_name)} --pairing-id ${quote(pairing.id)}`;
}

export function ConnectComputer() {
  const { workspace } = useWorkspace();
  return <ComputerConnection key={workspace.id} />;
}

function ComputerConnection() {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const [open, setOpen] = useState(false);
  const cache = useQueryClient();
  const http = client.workspace(workspace.id);
  const [created, setCreated] = useState<Schema["PairingCreated"]>();
  const [code, setCode] = useState("");
  const create = useMutation({
    mutationFn: () =>
      http.POST("/api/v1/client-environments/pairings").then(data),
    onSuccess: (value) => {
      setCreated(value);
      setCode("");
      approve.reset();
      cancel.reset();
      window.open(value.launcher_url, "_self");
    },
  });
  const pairing = useQuery({
    queryKey: ["client-pairing", workspace.id, created?.id],
    queryFn: ({ signal }) =>
      http
        .GET("/api/v1/client-environments/pairings/{pairing_id}", {
          params: { path: { pairing_id: created!.id } },
          signal,
        })
        .then(data),
    enabled: open && !!created,
    retry: false,
    refetchInterval: (query) =>
      query.state.error ||
      ["approved", "cancelled"].includes(query.state.data?.state ?? "")
        ? false
        : 2000,
  });
  const current = pairing.data ?? created;
  const approve = useMutation({
    mutationFn: () =>
      http
        .POST("/api/v1/client-environments/pairings/{pairing_id}/approve", {
          params: { path: { pairing_id: created!.id } },
          body: { verification_code: code },
        })
        .then(data),
    onSuccess: (value) => {
      cache.setQueryData(["client-pairing", workspace.id, created?.id], value);
      void cache.invalidateQueries({ queryKey: ["environments"] });
      void cache.invalidateQueries({ queryKey: ["mount-environment-options"] });
    },
  });
  const cancel = useMutation({
    mutationFn: () =>
      http
        .POST("/api/v1/client-environments/pairings/{pairing_id}/cancel", {
          params: { path: { pairing_id: created!.id } },
        })
        .then(data),
    onSuccess: (value) =>
      cache.setQueryData(["client-pairing", workspace.id, created?.id], value),
  });
  const environment = useQuery({
    queryKey: [
      "paired-environment",
      workspace.id,
      current?.id,
      current?.environment_id,
    ],
    queryFn: ({ signal }) =>
      http
        .GET("/api/v1/client-environments/{environment_id}", {
          params: { path: { environment_id: current!.environment_id! } },
          signal,
        })
        .then(data),
    enabled: current?.state === "approved" && !!current.environment_id,
    refetchInterval: (query) => {
      if (open) return 2000;
      // Finish observing an accepted connection even if the dialog was closed.
      return query.state.data?.environment.client_connection?.state ===
        "online" ||
        !created ||
        Date.parse(created.expires_at) <= Date.now()
        ? false
        : 2000;
    },
  });
  const connectedId =
    environment.data?.environment.client_connection?.state === "online"
      ? current?.environment_id
      : undefined;
  useEffect(() => {
    if (!connectedId) return;
    void cache.invalidateQueries({ queryKey: ["environments", workspace.id] });
    void cache.invalidateQueries({ queryKey: ["mount-environment-options"] });
  }, [cache, workspace.id, connectedId]);
  const expired = !!created && Date.parse(created.expires_at) <= Date.now();
  const finished =
    current?.state === "cancelled" ||
    (current?.state !== "approved" && (expired || !!pairing.error));
  return (
    <ModalFrame
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (next && (!created || finished) && !create.isPending)
          create.mutate();
      }}
      size="md"
      title={t("Connect my computer")}
      description={t("Give an agent access to this computer through Envd.")}
      closeLabel={t("Close")}
      trigger={
        <Button variant="outline">
          <LaptopIcon aria-hidden="true" />
          {t("Connect my computer")}
        </Button>
      }
    >
      <div className="grid gap-4">
        <p className="text-sm text-muted-foreground">
          {t(
            "Users and service accounts with Run permission in this workspace can use this computer after approval.",
          )}
        </p>
        {!created || finished ? (
          <>
            <p className="text-sm text-muted-foreground">
              {t(
                "Install Envd on your computer first. The macOS app shows the verification code and connection status; keep the app running while the agent uses your computer.",
              )}
            </p>
            <a
              className="text-sm underline"
              href={guide}
              target="_blank"
              rel="noreferrer"
            >
              {t("Install Envd and the macOS launcher")}
            </a>
            {finished && (
              <p role="status">
                {t(
                  "This pairing is no longer active. Create a new request to try again.",
                )}
              </p>
            )}
            <Button disabled={create.isPending} onClick={() => create.mutate()}>
              {t(create.isPending ? "Creating…" : "Start connection")}
            </Button>
          </>
        ) : current?.state === "approved" ? (
          <>
            <p role="status">
              {t(
                current.reused
                  ? "Computer already registered"
                  : "Computer approved",
              )}
            </p>
            {environment.data?.environment.client_connection?.state !==
              "online" && (
              <p className="text-sm text-muted-foreground">
                {t(
                  current.reused
                    ? "This computer is registered. If it is offline, reconnect in Envd; you do not need to pair again."
                    : "Waiting for Envd to finish startup and connect. If desktop access is enabled, complete the macOS permission prompts first.",
                )}
              </p>
            )}
            {environment.data && (
              <div className="flex items-center justify-between gap-3">
                <span>{environment.data.environment.name}</span>
                <EnvironmentStatus environment={environment.data.environment} />
              </div>
            )}
            <p className="text-sm text-muted-foreground">
              {t(
                "Select this environment in your agent or conversation settings. Approving the computer does not start a task.",
              )}
            </p>
            <ErrorNotice error={environment.error} />
            <Button variant="outline" onClick={() => setOpen(false)}>
              {t("Close")}
            </Button>
          </>
        ) : (
          <>
            <p className="text-sm text-muted-foreground">
              {t("Workspace")}: {workspace.name}
            </p>
            <Button render={<a href={created.launcher_url} />}>
              {t("Open A13N Envd")}
            </Button>
            <p className="text-xs text-muted-foreground">
              {t(
                "Nothing opened? Install the macOS launcher, or use the terminal command below.",
              )}
            </p>
            <details>
              <summary className="text-sm">
                {t("Connect from a terminal")}
              </summary>
              <div className="mt-3 grid gap-2">
                <CodeBlock>
                  <code className="language-shell">
                    {pairingCommand(created)}
                  </code>
                </CodeBlock>
                <p className="text-xs text-muted-foreground">
                  {t(
                    "The command uses Envd's local configuration. Enable Shell and desktop access locally if needed; the link cannot grant these permissions.",
                  )}
                </p>
              </div>
            </details>
            <small className="text-muted-foreground">
              {t("Expires")}: <Timestamp value={created.expires_at} />
            </small>
            {current?.device ? (
              <>
                <dl className="grid gap-2 rounded-lg border border-border p-3 text-sm">
                  <div>
                    <dt className="text-xs text-muted-foreground">
                      {t("Computer")}
                    </dt>
                    <dd>{current.device.name}</dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">
                      {t("Working directory")}
                    </dt>
                    <dd className="break-all">
                      {current.device.default_working_directory}
                    </dd>
                  </div>
                  <div>
                    <dt className="text-xs text-muted-foreground">
                      {t("Local permissions")}
                    </dt>
                    <dd>
                      {t(
                        current.device.full_control
                          ? "Files and Shell"
                          : "Files and configured commands",
                      )}
                      {current.device.computer_use &&
                        ` · ${t("Desktop control")}`}
                    </dd>
                  </div>
                </dl>
                <p className="text-xs text-muted-foreground">
                  {t(
                    "Only approve your own computer. The working directory does not restrict file access; desktop access includes other applications and screenshots.",
                  )}
                </p>
                <FormField
                  label={t("Verification code")}
                  description={t(
                    "Enter the six-digit code shown in the Envd app or terminal.",
                  )}
                >
                  <Input
                    value={code}
                    onChange={(event) => setCode(event.target.value)}
                    inputMode="numeric"
                    autoComplete="off"
                    maxLength={6}
                  />
                </FormField>
                <Button
                  disabled={approve.isPending || !/^\d{6}$/.test(code)}
                  onClick={() => approve.mutate()}
                >
                  {t(approve.isPending ? "Approving…" : "Approve computer")}
                </Button>
              </>
            ) : (
              <p role="status" className="text-sm">
                {t(
                  "Waiting for Envd to check this computer's saved registration. Open the app if prompted; registered computers do not need pairing again.",
                )}
              </p>
            )}
            <Button
              variant="ghost"
              disabled={
                cancel.isPending ||
                approve.isPending ||
                current?.state === "approving"
              }
              onClick={() => cancel.mutate()}
            >
              {t("Cancel connection")}
            </Button>
          </>
        )}
        <ErrorNotice
          error={create.error ?? approve.error ?? cancel.error ?? pairing.error}
        />
      </div>
    </ModalFrame>
  );
}
