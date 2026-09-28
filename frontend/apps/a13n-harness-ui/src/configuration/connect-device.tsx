import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { NewResourceButton } from "./sources";
import { useDeviceInfo } from "./device-directory";

import { connectCommand } from "./device-command";

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
      description="Run envd on the computer whose directories you want to use, then approve its verification code here."
      closeLabel="Close"
      trigger={
        <Button type="button" variant={onConnected ? "outline" : "default"}>
          {label}
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
    mutationFn: () => navigator.clipboard.writeText(command),
    onSuccess: () => setCopied(command),
  });
  return (
    <div className="flex flex-col gap-5">
      {!approved ? (
        <>
          <section className="flex flex-col gap-3">
            <h3 className="font-medium">1. Run on the Device</h3>
            <p className="text-sm text-muted-foreground">
              Install a13n-envd first and make sure it is on your PATH. This
              command runs in the foreground; keep the terminal open. It does
              not install a background service.
            </p>
            <ChoiceField
              label="Device terminal"
              value={shell}
              onValueChange={(value) => setShell(value as typeof shell)}
              options={[
                { value: "posix", label: "macOS / Linux · POSIX shell" },
                { value: "powershell", label: "Windows · PowerShell" },
              ]}
            />
            <label className="flex items-start gap-2">
              <input
                type="checkbox"
                checked={execution}
                onChange={(event) => setExecution(event.target.checked)}
              />
              Enable shell execution as the Device user (Full Control, not a
              sandbox)
            </label>
            <p className="text-sm text-muted-foreground">
              Pairing alone does not enable shell execution. The Harness
              server’s local mode does not change Device permissions.
            </p>
            <details>
              <summary>Desktop access</summary>
              <label className="flex items-start gap-2 mt-2">
                <input
                  type="checkbox"
                  checked={desktop}
                  onChange={(event) => setDesktop(event.target.checked)}
                />
                Enable desktop observation and control
              </label>
              <p className="text-sm text-muted-foreground">
                Independent of shell access. Requires platform desktop
                permissions. Screenshots can be sent to the model and retained
                in history.
              </p>
            </details>
            <pre className="overflow-x-auto rounded-lg bg-muted p-3">
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
            <p className="text-sm text-muted-foreground">
              This command uses the address open in your browser. It must be
              reachable from the Device. Remote connections require HTTPS;
              localhost refers to the Device itself.
            </p>
            <details>
              <summary>Reconnect and multiple Hosts</summary>
              <p>
                Rerun this same command and environment after restarting. Saved
                identity and credentials are reused, but the saved Host
                connection does not retain every launch flag or shell/desktop
                setting. Revoked credentials are not automatically replaced.
              </p>
              <p>
                For another Host, run a separate envd process with a different{" "}
                <code>--instance</code>. Use <code>--host</code> to name the
                saved connection.
              </p>
            </details>
          </section>
          <section className="flex flex-col gap-3">
            <h3 className="font-medium">2. Verify and approve</h3>
            <p className="text-sm text-muted-foreground">
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
          <details>
            <summary>Manual HTTP or WebSocket connection</summary>
            <p>
              For an existing daemon or credentials you manage yourself. Return
              to the Device picker after saving.
            </p>
            <NewResourceButton kind="device" label="Configure manually" />
          </details>
        </>
      ) : (
        <section className="flex flex-col gap-3">
          <h3 className="font-medium">3. {approved.name}</h3>
          <p role="status">
            {info.data?.available
              ? "Device online"
              : "Approved · waiting for Device connection"}
          </p>
          <ErrorNotice error={info.error} />
          {info.data && !info.data.available && (
            <p className="text-sm text-muted-foreground">
              Keep envd running and check its terminal. Approval registers the
              Device; it does not prove the connection is ready.
            </p>
          )}
          <p className="text-sm text-muted-foreground">
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
