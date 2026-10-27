import {
  SettingsChoice as ChoiceField,
  SettingsSection,
  SettingsToggle,
} from "../shell/settings";
import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { NewResourceButton } from "./sources";
import { useDeviceInfo } from "./device-directory";

import { connectCommand } from "./device-command";
import { copyText } from "../platform";
import layout from "../shell/workbench.module.css";
import settingsLayout from "./settings.module.css";
import styles from "./connect-device.module.css";

export function ConnectDevice({
  onConnected,
  label = "Connect Device",
}: {
  onConnected?: (device: Schema<"DeviceSummary">) => void;
  label?: string;
}) {
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title="Connect a Device"
      description="Connect another computer by running envd, then verify its code."
      closeLabel="Close dialog"
      size="lg"
      trigger={
        <Button type="button" variant={onConnected ? "outline" : "default"}>
          {label}
        </Button>
      }
      footer={
        <Button variant="outline" onClick={() => setOpen(false)}>
          Close
        </Button>
      }
    >
      {open && (
        <ConnectDeviceSteps
          onConnected={(device) => {
            onConnected?.(device);
            setOpen(false);
          }}
          inline={!!onConnected}
        />
      )}
    </ModalFrame>
  );
}

function ConnectDeviceSteps({
  onConnected,
  inline,
}: {
  onConnected: (device: Schema<"DeviceSummary">) => void;
  inline: boolean;
}) {
  const { client } = useTransport();
  const [shell, setShell] = useState<"posix" | "powershell">("posix");
  const [execution, setExecution] = useState(false);
  const [desktop, setDesktop] = useState(false);
  const [approved, setApproved] = useState<Schema<"DeviceSummary">>();
  const [copied, setCopied] = useState("");
  const pairings = useQuery({
    queryKey: ["device-pairings"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/device-pairings", { signal })),
    refetchInterval: 2000,
    enabled: !approved,
  });
  const info = useDeviceInfo(approved?.id ?? "", true, true);
  const command = connectCommand(
    window.location.origin,
    shell,
    execution,
    desktop,
  );
  const copy = useMutation({
    mutationFn: () => copyText(command),
    onSuccess: () => setCopied(command),
  });
  return (
    <div className={styles.steps}>
      {!approved ? (
        <>
          <section className={styles.step}>
            <h3>1. Run on the device</h3>
            <p>
              Install a13n-envd on your PATH, then run this command. Keep the
              terminal open; it does not install a background service.
            </p>
            <SettingsSection>
              <ChoiceField
                label="Device terminal"
                value={shell}
                onValueChange={(value) => setShell(value as typeof shell)}
                options={[
                  { value: "posix", label: "macOS / Linux · POSIX shell" },
                  { value: "powershell", label: "Windows · PowerShell" },
                ]}
              />
              <SettingsToggle
                label="Enable shell execution"
                description="Full Control as the device user, without a sandbox. Off by default; independent of server permissions."
                checked={execution}
                onCheckedChange={setExecution}
              />
              <details className={settingsLayout.groupDisclosure}>
                <summary>Desktop access</summary>
                <div>
                  <SettingsToggle
                    label="Enable desktop observation and control"
                    description="Independent of shell access. Requires platform desktop permissions. Screenshots can be sent to the model and retained in history."
                    checked={desktop}
                    onCheckedChange={setDesktop}
                  />
                </div>
              </details>
            </SettingsSection>
            <pre className={styles.command}>
              <code>{command}</code>
            </pre>
            <div>
              <Button
                type="button"
                variant="outline"
                onClick={() => copy.mutate()}
                loading={copy.isPending}
              >
                {copied === command ? "Copied" : "Copy command"}
              </Button>
            </div>
            <ErrorNotice error={copy.error} />
            <p>
              Use a server address the device can reach. Remote connections
              require HTTPS; localhost points to the device itself.
            </p>
            <details className={layout.details}>
              <summary>Reconnecting and multiple hosts</summary>
              <p>
                After restarting, rerun the same command and environment.
                Identity and credentials are reused; supply launch flags and
                access settings again. Replace revoked credentials.
              </p>
              <p>
                For another Host, run a separate envd process with a different{" "}
                <code>--instance</code>. Use <code>--host</code> to name the
                saved connection.
              </p>
            </details>
          </section>
          <section className={styles.step}>
            <h3>2. Verify and approve</h3>
            <p>
              Approve only the code shown in your Device terminal. Closing this
              dialog does not reject a pending request.
            </p>
            <ErrorNotice error={pairings.error} />
            {!pairings.data?.length && (
              <p role="status">
                Waiting for a verification request. If a request disappears, it
                may have expired or been handled elsewhere; that is not proof of
                approval.
              </p>
            )}
            {pairings.data?.map((pairing) => (
              <PendingDevice
                key={pairing.pairing_id}
                pairing={pairing}
                onApproved={setApproved}
              />
            ))}
          </section>
          <details className={layout.details}>
            <summary>Connect manually</summary>
            <p>
              For an existing daemon or credentials you manage yourself. Return
              to the Device picker after saving.
            </p>
            <NewResourceButton kind="device" label="Configure manually" />
          </details>
        </>
      ) : (
        <section className={styles.step}>
          <h3>3. {approved.name}</h3>
          <p role="status">
            {info.data?.available
              ? "Device online"
              : "Approved · waiting for Device connection"}
          </p>
          <ErrorNotice error={info.error} />
          {info.data && !info.data.available && (
            <p>
              Keep envd running and check its terminal. Approval registers the
              Device; it does not prove the connection is ready.
            </p>
          )}
          <p>
            This Device is now registered globally. Closing this dialog keeps
            the registration
            {inline ? " but does not save an environment binding" : ""}.
          </p>
          <Button
            type="button"
            disabled={!info.data?.available}
            onClick={() => onConnected(approved)}
          >
            {inline ? "Choose a directory" : "Done"}
          </Button>
        </section>
      )}
    </div>
  );
}

export function PendingDevice({
  pairing,
  onApproved,
}: {
  pairing: Schema<"PairingChallenge">;
  onApproved?: (device: Schema<"DeviceSummary">) => void;
}) {
  const { client } = useTransport();
  const cache = useQueryClient();
  const decision = useMutation({
    mutationFn: async (approve: boolean) => {
      const params = { path: { pairing_id: pairing.pairing_id } };
      if (approve)
        return result(
          client.POST("/api/device-pairings/{pairing_id}/approve", { params }),
        );
      await client.POST("/api/device-pairings/{pairing_id}/reject", { params });
      return undefined;
    },
    onSuccess: (device) => {
      void cache.invalidateQueries();
      if (device) onApproved?.(device);
    },
  });
  return (
    <div className="rounded-xl bg-muted p-4">
      <div className="flex flex-wrap items-center justify-between gap-3">
        <div>
          <strong>{pairing.name}</strong>
          <p className="text-sm text-muted-foreground">{pairing.device_id}</p>
          <p>
            Verification code: <code>{pairing.verification_code}</code>
          </p>
          <p className="text-xs text-muted-foreground">
            Expires {new Date(pairing.expires_at).toLocaleTimeString()}
          </p>
        </div>
        <div className="flex gap-2">
          <Button
            type="button"
            variant="ghost"
            disabled={decision.isPending}
            onClick={() => decision.mutate(false)}
          >
            Reject
          </Button>
          <Button
            type="button"
            variant="outline"
            disabled={decision.isPending}
            onClick={() => decision.mutate(true)}
          >
            Approve Device
          </Button>
        </div>
      </div>
      <ErrorNotice error={decision.error} />
    </div>
  );
}
