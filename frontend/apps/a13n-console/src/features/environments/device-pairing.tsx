import { PlugIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import {
  Button,
  DisclosureSection,
  FormField,
  Input,
  ModalFrame,
  SearchPicker,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useNavigate, useSearchParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { CopyButton } from "../../shared/identity";
import styles from "../../shared/shared.module.css";
import { workspacePath } from "../../shared/paths";
import { pairingFromSearch } from "./pairing-link";
import { useEnvironmentTypes } from "./providers";

export function ConnectDevice({
  onApproved,
}: {
  onApproved: (environment: Schema["Environment"]) => void;
}) {
  const { t } = useTranslation();
  const [params, setParams] = useSearchParams();
  const linkedPairing = pairingFromSearch(params.toString());
  const types = useEnvironmentTypes();
  const supported = types.data?.items.some(
    (item) => item.type === "websocket_envd",
  );
  const [opened, setOpened] = useState(false);
  const open = opened || !!linkedPairing;
  function close() {
    setOpened(false);
    if (linkedPairing) {
      setParams(
        (current) => {
          const next = new URLSearchParams(current);
          next.delete("envd_pairing");
          return next;
        },
        { replace: true },
      );
    }
  }
  return (
    <ModalFrame
      open={open}
      onOpenChange={(value) => (value ? setOpened(true) : close())}
      trigger={
        <Button type="button">
          <PlugIcon aria-hidden="true" />
          {t("Connect device")}
        </Button>
      }
      title={t("Connect an envd device")}
      description={t(
        "Connect your computer once, then choose its directories when starting a conversation.",
      )}
      closeLabel={t("Close")}
      size="md"
      placement="top"
    >
      {open &&
        (types.isPending ? (
          <Loading variant="form" rows={2} />
        ) : types.error ? (
          <ErrorNotice error={types.error} />
        ) : supported ? (
          <PairingForm
            initialPairing={linkedPairing}
            close={close}
            onApproved={onApproved}
          />
        ) : (
          <p className="text-sm text-muted-foreground">
            {t(
              "Device connections are unavailable on this Service. Ask the operator to enable the WebSocket envd provider with Redis.",
            )}
          </p>
        ))}
    </ModalFrame>
  );
}

function PairingForm({
  initialPairing,
  close,
  onApproved,
}: {
  initialPairing?: string;
  close: () => void;
  onApproved: (environment: Schema["Environment"]) => void;
}) {
  const { t } = useTranslation();
  const { workspace, workspaces } = useWorkspace();
  const client = useClient(),
    cache = useQueryClient(),
    navigate = useNavigate();
  const [workspaceId, setWorkspaceId] = useState(workspace.id);
  const [input, setInput] = useState(initialPairing ?? "");
  const [pairingId, setPairingId] = useState(initialPairing);
  const [inputError, setInputError] = useState<Error>();
  const command = `a13n-envd connect ${window.location.origin} --host service --instance service`;
  const challenge = useQuery({
    queryKey: ["device-pairing", workspaceId, pairingId],
    enabled: !!pairingId,
    retry: false,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/device-pairings/{pairing_id}", {
          params: { path: { workspace: workspaceId, pairing_id: pairingId! } },
          signal,
        })
        .then(data),
  });
  const approve = useMutation({
    mutationFn: () =>
      client.http
        .POST(
          "/api/v1/workspaces/{workspace}/device-pairings/{pairing_id}/approve",
          {
            params: {
              path: { workspace: workspaceId, pairing_id: pairingId! },
            },
          },
        )
        .then(data),
    onSuccess: (environment) => {
      void cache.invalidateQueries({ queryKey: ["environments"] });
      void cache.invalidateQueries({
        queryKey: ["environment-provider-options"],
      });
      void cache.invalidateQueries({ queryKey: ["run-options"] });
      close();
      const destination = workspaces?.find(
        (item) => item.id === environment.workspace_id,
      );
      if (destination && destination.id !== workspace.id) {
        navigate(`${workspacePath(destination)}/environments/instances`);
      }
      onApproved(environment);
    },
  });
  const reject = useMutation({
    mutationFn: () =>
      client.http.POST(
        "/api/v1/workspaces/{workspace}/device-pairings/{pairing_id}/reject",
        {
          params: { path: { workspace: workspaceId, pairing_id: pairingId! } },
        },
      ),
    onSuccess: close,
  });
  function inspect() {
    const value = input.trim();
    let id = /^pair-[0-9a-f]{24}$/.test(value) ? value : undefined;
    if (!id) {
      try {
        const url = new URL(value, window.location.origin);
        if (url.origin === window.location.origin)
          id = pairingFromSearch(url.search);
      } catch {
        /* Show the same actionable validation message below. */
      }
    }
    if (!id) {
      setInputError(
        new Error(t("Paste the pairing link printed by envd for this Host.")),
      );
      return;
    }
    setInputError(undefined);
    setPairingId(id);
  }
  const pending = approve.isPending || reject.isPending;
  return (
    <div className={styles.stack}>
      {!initialPairing && (
        <>
          <p>
            {t(
              "Run this on the computer where envd is installed. Keep the process running.",
            )}
          </p>
          <div className="flex min-w-0 items-start gap-2 rounded-md border bg-muted/40 p-3">
            <code className="min-w-0 flex-1 break-all text-xs">{command}</code>
            <CopyButton value={command} copyLabel={t("Copy command")} />
          </div>
          <p className="text-sm text-muted-foreground">
            {t(
              "Open the approval link printed in the terminal, or paste it below. Only approve a code that matches your terminal.",
            )}
          </p>
          <DisclosureSection title={t("Connection options")}>
            <div className="grid gap-3 text-sm text-muted-foreground">
              <p>
                {t(
                  "For unrestricted local shell execution, set A13N_ENVD_FULL_CONTROL=1 before starting envd, or use an explicit envd configuration. This uses the operating-system account's authority; a working directory is not a sandbox.",
                )}
              </p>
              <p>
                {t(
                  "Each envd process connects to one Host. Use a different --instance for another process on the same computer. Reconnect with the same --host and --instance to retain its identity and credential.",
                )}
              </p>
              <p>
                {t(
                  "The Host URL must be reachable from that computer. HTTPS is required except on loopback; localhost always means the computer running envd.",
                )}
              </p>
            </div>
          </DisclosureSection>
          <form
            className="flex items-end gap-2"
            onSubmit={(event) => {
              event.preventDefault();
              inspect();
            }}
          >
            <FormField label={t("Pairing link")} className="min-w-0 flex-1">
              <Input
                value={input}
                onChange={(event) => setInput(event.target.value)}
                autoComplete="off"
              />
            </FormField>
            <Button type="submit" variant="outline" disabled={!input.trim()}>
              {t("Review device")}
            </Button>
          </form>
        </>
      )}
      <FormField
        label={t("Workspace")}
        description={t(
          "Approval grants this Workspace access to the device. Choose the destination before approving.",
        )}
      >
        <SearchPicker
          label={t("Workspace")}
          placeholder={t("Select workspace")}
          emptyMessage={t("No options")}
          value={workspaceId}
          onValueChange={setWorkspaceId}
          disabled={pending}
          groups={[
            {
              label: t("Workspaces"),
              options: (workspaces ?? [workspace]).map((item) => ({
                value: item.id,
                label: item.name,
              })),
            },
          ]}
        />
      </FormField>
      <ErrorNotice
        error={inputError ?? challenge.error ?? approve.error ?? reject.error}
      />
      {pairingId && challenge.isPending && <Loading variant="form" rows={2} />}
      {challenge.data && (
        <>
          <section className="grid gap-2 rounded-md border p-4">
            <strong>{challenge.data.name}</strong>
            <span className="break-all text-xs text-muted-foreground">
              {challenge.data.device_id}
            </span>
            <p className="text-sm">
              {t(
                "Verify this code matches your envd terminal before approving.",
              )}
            </p>
            <code className="text-2xl font-medium tracking-widest">
              {challenge.data.verification_code}
            </code>
            <span className="text-xs text-muted-foreground">
              {t("Expires")} <Timestamp value={challenge.data.expires_at} />
            </span>
          </section>
          <div className="flex justify-end gap-2">
            <Button
              variant="outline"
              disabled={pending}
              onClick={() => reject.mutate()}
            >
              {t("Reject device")}
            </Button>
            <Button disabled={pending} onClick={() => approve.mutate()}>
              {t("Approve device")}
            </Button>
          </div>
        </>
      )}
    </div>
  );
}
