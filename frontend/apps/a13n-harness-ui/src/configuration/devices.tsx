import { useState } from "react";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useSources, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { NewResourceButton, DraftLinks } from "./sources";
import { useDeviceInfo } from "./device-directory";
import { ForgetEnvironment } from "./forget-environment";
import styles from "../shell/workbench.module.css";

export function DevicesSection() {
  const { client } = useTransport();
  const sources = useSources();
  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: ({ signal }) => result(client.GET("/api/devices", { signal })),
    refetchInterval: 5000,
  });
  const pairings = useQuery({
    queryKey: ["device-pairings"],
    queryFn: ({ signal }) =>
      result(client.GET("/api/device-pairings", { signal })),
    refetchInterval: 2000,
  });
  return (
    <section className={styles.stack}>
      <div className="flex items-center justify-between gap-3">
        <h2>Devices</h2>
        <ConnectDevice />
      </div>
      <p>
        Connect envd once, then select its working directories in Project
        defaults or a conversation. Each envd process connects to one Host.
      </p>
      <ErrorNotice error={devices.error || pairings.error} />
      {!!pairings.data?.length && (
        <div className={styles.stack}>
          <h3>Waiting for approval</h3>
          <p>
            Approve only when the verification code matches your envd terminal.
          </p>
          {pairings.data.map((pairing) => (
            <PendingDevice key={pairing.pairing_id} pairing={pairing} />
          ))}
        </div>
      )}
      <DraftLinks kinds={["device"]} />
      {devices.data?.length === 0 && <p>No Devices connected yet.</p>}
      {devices.data?.map((device) => {
        const source = sources.data?.sources.find(
          (item) =>
            item.resource_kind === "device" &&
            item.resource_ids.includes(device.id),
        );
        return (
          <div className={styles.resourceRow} key={device.id}>
            <div>
              <strong>{device.name}</strong>
              <small>
                {device.id} · {device.transport}
              </small>
              {device.registration === "revoked" ? (
                <p className="text-sm text-muted-foreground">
                  Revoked · connections are blocked
                </p>
              ) : (
                <DeviceStatus deviceId={device.id} />
              )}
            </div>
            <div className="flex flex-wrap items-center gap-2">
              {device.registration === "paired" && (
                <RevokeDevice device={device} />
              )}
              {source && (
                <>
                  <Link
                    to={`/settings/source?path=${encodeURIComponent(source.relative_path)}`}
                  >
                    Edit connection
                  </Link>
                  <ForgetEnvironment
                    path={source.relative_path}
                    name={device.name}
                    resourceId={device.id}
                  />
                </>
              )}
            </div>
          </div>
        );
      })}
    </section>
  );
}

function ConnectDevice() {
  const command = `a13n-envd connect ${window.location.origin}`;
  const [copied, setCopied] = useState(false);
  const copy = useMutation({
    mutationFn: () => navigator.clipboard.writeText(command),
    onSuccess: () => setCopied(true),
  });
  return (
    <ModalFrame
      title="Connect a Device"
      description="Run this command on the computer where envd is installed. Keep envd running to use its directories."
      closeLabel="Close"
      trigger={<Button type="button">Connect Device</Button>}
    >
      <div className={styles.stack}>
        <pre className="overflow-x-auto rounded-lg bg-muted p-3">
          <code>{command}</code>
        </pre>
        <Button
          variant="outline"
          onClick={() => copy.mutate()}
          loading={copy.isPending}
        >
          {copied ? "Copied" : "Copy command"}
        </Button>
        <ErrorNotice error={copy.error} />
        <p>
          Return here and approve the matching verification code. Reuse the same
          command after a restart; no new approval is needed.
        </p>
        <p>
          The address must be reachable from that computer. Remote connections
          require HTTPS; localhost only works on this computer.
        </p>
        <details>
          <summary>Shell access and multiple Hosts</summary>
          <p>
            Set <code>A13N_ENVD_FULL_CONTROL=1</code> in envd's environment to
            enable the native shell with the launching user's authority. This is
            independent of this Host's local execution profile.
          </p>
          <p>
            For another Host on the same machine, run another envd process with
            a different <code>--instance</code> name. Use <code>--host</code> to
            name the saved Host connection.
          </p>
        </details>
        <details>
          <summary>Manual connection</summary>
          <p>
            For an existing HTTP daemon or a credential you manage yourself:
          </p>
          <NewResourceButton kind="device" label="Configure manually" />
        </details>
      </div>
    </ModalFrame>
  );
}

function PendingDevice({ pairing }: { pairing: Schema<"PairingChallenge"> }) {
  const { client } = useTransport();
  const cache = useQueryClient();
  const decision = useMutation({
    mutationFn: async (approve: boolean) => {
      const params = { path: { pairing_id: pairing.pairing_id } };
      if (approve) {
        await client.POST("/api/device-pairings/{pairing_id}/approve", {
          params,
        });
      } else {
        await client.POST("/api/device-pairings/{pairing_id}/reject", {
          params,
        });
      }
    },
    onSuccess: () => {
      void cache.invalidateQueries();
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
            variant="ghost"
            disabled={decision.isPending}
            onClick={() => decision.mutate(false)}
          >
            Reject
          </Button>
          <Button
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

function RevokeDevice({ device }: { device: Schema<"DeviceSummary"> }) {
  const { client } = useTransport();
  const cache = useQueryClient();
  const [open, setOpen] = useState(false);
  const revoke = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/devices/{device_id}/revoke", {
          params: { path: { device_id: device.id } },
        }),
      ),
    onSuccess: () => {
      setOpen(false);
      void cache.invalidateQueries();
    },
  });
  return (
    <ModalFrame
      open={open}
      onOpenChange={(next) => {
        if (!revoke.isPending) {
          setOpen(next);
          revoke.reset();
        }
      }}
      title={`Revoke ${device.name}?`}
      description="Disconnect this Device and block its saved credential. Runs using it lose their connection. Remote files and captured history are preserved."
      closeLabel="Cancel"
      trigger={
        <Button size="sm" variant="ghost">
          Revoke
        </Button>
      }
      footer={
        <>
          <Button
            variant="outline"
            disabled={revoke.isPending}
            onClick={() => setOpen(false)}
          >
            Cancel
          </Button>
          <Button
            variant="destructive"
            loading={revoke.isPending}
            onClick={() => revoke.mutate()}
          >
            Revoke connection
          </Button>
        </>
      }
    >
      <p>
        This revokes trust, rather than only forgetting local configuration. The
        daemon will not automatically request a new credential.
      </p>
      <ErrorNotice error={revoke.error} />
    </ModalFrame>
  );
}

function DeviceStatus({ deviceId }: { deviceId: string }) {
  const info = useDeviceInfo(deviceId, false);
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span>
        {info.data
          ? info.data.available
            ? "Online"
            : "Unavailable"
          : "Not checked"}
      </span>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        loading={info.isFetching}
        onClick={() => void info.refetch()}
      >
        Check connection
      </Button>
      <ErrorNotice error={info.error} />
    </div>
  );
}
