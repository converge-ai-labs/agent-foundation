import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useSources, useTransport } from "../transport/context";
import { result } from "../transport/client";
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
  });
  return (
    <section className={styles.stack}>
      <div className="flex items-center justify-between gap-3">
        <h2>Devices</h2>
        <NewResourceButton kind="device" label="Connect Device" />
      </div>
      <p>
        Connect envd once, then add its working directories in Project defaults
        or a conversation's next Run selections. Forgetting a connection works
        offline and never deletes remote files.
      </p>
      <DraftLinks kinds={["device"]} />
      <ErrorNotice error={devices.error} />
      {devices.data?.length === 0 && <p>No Devices configured.</p>}
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
              <DeviceStatus deviceId={device.id} />
            </div>
            {source && (
              <div className="flex items-center gap-2">
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
              </div>
            )}
          </div>
        );
      })}
    </section>
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
