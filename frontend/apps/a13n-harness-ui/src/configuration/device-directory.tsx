import { useTranslation } from "../i18n";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, ScrollArea } from "a13n-ui";
import { FolderIcon } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";

export function useDeviceInfo(deviceId: string, enabled = true, poll = false) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["device", deviceId],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/devices/{device_id}", {
          params: { path: { device_id: deviceId } },
          signal,
        }),
      ),
    enabled: enabled && !!deviceId,
    retry: false,
    refetchInterval: poll ? 2000 : false,
  });
}

export function DeviceDirectory({
  deviceId,
  value,
  onChange,
  label,
}: {
  deviceId: string;
  value: string;
  onChange: (path: string) => void;
  label?: string;
}) {
  const { client } = useTransport();
  const { t } = useTranslation();
  const info = useDeviceInfo(deviceId);
  const [location, setLocation] = useState<{ path: string; offset: number }>();
  const directories = useQuery({
    queryKey: ["device-directories", deviceId, location],
    enabled: !!location,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/devices/{device_id}/directories", {
          params: {
            path: { device_id: deviceId },
            query: {
              path: location!.path,
              offset: location!.offset,
              limit: 50,
            },
          },
          signal,
        }),
      ),
    retry: false,
  });
  const page = directories.data;
  const open = (path: string) => setLocation({ path, offset: 0 });
  const defaultPath = info.data?.default_working_directory;
  return (
    <section className="flex flex-col gap-3" aria-label={t("Device directory")}>
      <TextField
        label={label ?? t("Working directory")}
        value={value}
        onChange={onChange}
        description={t(
          "Enter a full path on the device. This folder does not restrict access to other files.",
        )}
      />
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          size="sm"
          variant="outline"
          disabled={!info.data?.available || !defaultPath}
          onClick={() => onChange(defaultPath!)}
        >
          {t("Use Device default")}
        </Button>
        {info.data?.available && info.data.directory_discovery && (
          <Button
            type="button"
            size="sm"
            variant="outline"
            onClick={() => open(value || defaultPath!)}
          >
            <FolderIcon />
            {t("Browse directories")}
          </Button>
        )}
      </div>
      {info.isFetching && <p role="status">{t("Checking Device…")}</p>}
      {info.data && !info.data.available && (
        <div>
          <p role="status">
            {t(
              "Device offline. Keep or enter a folder path, or remove it from the selection.",
            )}
          </p>
          <Button
            type="button"
            size="sm"
            variant="ghost"
            loading={info.isFetching}
            onClick={() => void info.refetch()}
          >
            {t("Retry connection")}
          </Button>
        </div>
      )}
      {info.data?.available && !info.data.directory_discovery && (
        <p>{t("Folder browsing is off. Use the default or enter a path.")}</p>
      )}
      <ErrorNotice error={info.error} retry={() => void info.refetch()} />
      {location && (
        <div className="flex flex-col gap-2 rounded-lg bg-muted/40 p-3">
          <div className="flex items-center justify-between gap-2">
            <span className="break-all text-sm">
              {page?.path ?? location.path}
            </span>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => setLocation(undefined)}
            >
              {t("Close browser")}
            </Button>
          </div>
          <ErrorNotice
            error={directories.error}
            retry={() => void directories.refetch()}
          />
          {directories.isFetching && (
            <p role="status">{t("Loading directories…")}</p>
          )}
          {page && (
            <>
              <div className="flex flex-wrap gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="outline"
                  disabled={!page.parent_path}
                  onClick={() => open(page.parent_path!)}
                >
                  {t("Parent directory")}
                </Button>
                <Button
                  type="button"
                  size="sm"
                  onClick={() => {
                    onChange(page.path);
                    setLocation(undefined);
                  }}
                >
                  {t("Choose folder")}
                </Button>
              </div>
              <ScrollArea autoHeight scrollbarGutter className="max-h-64">
                {page.entries?.map((entry) => (
                  <Button
                    key={entry.path}
                    type="button"
                    variant="ghost"
                    className="w-full justify-start"
                    onClick={() => open(entry.path)}
                  >
                    <FolderIcon />
                    {entry.name}
                  </Button>
                ))}
                {!page.entries?.length && <p>{t("No subdirectories.")}</p>}
              </ScrollArea>
              <div className="flex justify-between gap-2">
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  disabled={location.offset === 0}
                  onClick={() => open(location.path)}
                >
                  {t("First page")}
                </Button>
                {page.next_offset != null && (
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    onClick={() =>
                      setLocation({
                        path: page.path,
                        offset: page.next_offset!,
                      })
                    }
                  >
                    {t("Next page")}
                  </Button>
                )}
              </div>
            </>
          )}
        </div>
      )}
    </section>
  );
}
