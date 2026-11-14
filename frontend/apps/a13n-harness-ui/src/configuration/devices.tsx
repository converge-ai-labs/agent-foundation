import { useTranslation } from "../i18n";
import { useState } from "react";
import { Link } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { useSources, useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { DebugId } from "../shell/debug";
import { DraftLinks } from "./sources";
import { useDeviceInfo } from "./device-directory";
import { ConnectDevice, PendingDevice } from "./connect-device";
import { ForgetEnvironment } from "./forget-environment";
import styles from "../shell/workbench.module.css";
import environmentStyles from "./environments-page.module.css";
import layout from "./settings.module.css";

export function DevicesSection() {
  const { t } = useTranslation();
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
      <div className={environmentStyles.sectionHeader}>
        <h2>{t("Devices")}</h2>
        <ConnectDevice />
      </div>
      <p>
        {t(
          "Use folders and tools on another computer, with that computer’s permissions.",
        )}
      </p>
      <ErrorNotice error={devices.error || pairings.error} />
      {!!pairings.data?.length && (
        <div className={styles.stack}>
          <h3>{t("Waiting for approval")}</h3>
          <p>
            {t(
              "Approve only when the verification code matches your envd terminal.",
            )}
          </p>
          {pairings.data.map((pairing) => (
            <PendingDevice key={pairing.pairing_id} pairing={pairing} />
          ))}
        </div>
      )}
      <DraftLinks kinds={["device"]} />
      {devices.data?.length === 0 && (
        <p className={layout.emptySection}>{t("No devices connected yet.")}</p>
      )}
      <div className={styles.resourceList}>
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
                  {device.transport}
                  <DebugId label={t("Device")} value={device.id} />
                </small>
                {device.registration === "revoked" ? (
                  <p className="text-sm text-muted-foreground">
                    {t("Revoked - connections are blocked")}
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
                      {t("Edit connection")}
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
      </div>
    </section>
  );
}

function RevokeDevice({ device }: { device: Schema<"DeviceSummary"> }) {
  const { t } = useTranslation();
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
      bottomStickOnMobile
      open={open}
      onOpenChange={(next) => {
        if (!revoke.isPending) {
          setOpen(next);
          revoke.reset();
        }
      }}
      title={t("Revoke {{name}}?", { name: device.name })}
      description={t(
        "Disconnect this device and revoke its credential? Active runs will lose access. Remote files and chat history are kept.",
      )}
      closeLabel={t("Close")}
      trigger={
        <Button size="sm" variant="ghost">
          {t("Revoke")}
        </Button>
      }
      footer={
        <>
          <Button
            variant="outline"
            disabled={revoke.isPending}
            onClick={() => setOpen(false)}
          >
            {t("Cancel")}
          </Button>
          <Button
            variant="destructive"
            loading={revoke.isPending}
            onClick={() => revoke.mutate()}
          >
            {t("Revoke connection")}
          </Button>
        </>
      }
    >
      <p>
        {t(
          "The device’s credential will stop working. Reconnect it manually to request a new one.",
        )}
      </p>
      <ErrorNotice error={revoke.error} />
    </ModalFrame>
  );
}

function DeviceStatus({ deviceId }: { deviceId: string }) {
  const { t } = useTranslation();
  const info = useDeviceInfo(deviceId, false);
  return (
    <div className="flex flex-wrap items-center gap-2 text-sm">
      <span>
        {info.data
          ? info.data.available
            ? t("Online")
            : t("Unavailable")
          : t("Not checked")}
      </span>
      <Button
        type="button"
        size="sm"
        variant="ghost"
        loading={info.isFetching}
        onClick={() => void info.refetch()}
      >
        {t("Check connection")}
      </Button>
      <ErrorNotice error={info.error} />
    </div>
  );
}
