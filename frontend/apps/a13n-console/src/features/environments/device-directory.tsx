import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, FormField, Input } from "a13n-ui";
import { FolderIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";

/** Observations only: choosing a directory never starts an execution. */
export function DeviceDirectory({
  environmentId,
  value,
  onChange,
}: {
  environmentId: string;
  value: string;
  onChange: (path: string) => void;
}) {
  const client = useClient();
  const { t } = useTranslation();
  const [location, setLocation] = useState<{ path: string; offset: number }>();
  const info = useQuery({
    queryKey: ["environment-device", environmentId],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environments/{environment_id}/device", {
          params: { path: { environment_id: environmentId } },
          signal,
        })
        .then(data),
    retry: false,
  });
  const directories = useQuery({
    queryKey: ["environment-directories", environmentId, location],
    enabled: location !== undefined,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environments/{environment_id}/directories", {
          params: {
            path: { environment_id: environmentId },
            query: {
              path: location!.path,
              offset: location!.offset,
              limit: 50,
            },
          },
          signal,
        })
        .then(data),
    retry: false,
  });
  const page = directories.data;
  const open = (path: string) => setLocation({ path, offset: 0 });
  return (
    <section className="flex flex-col gap-3" aria-label={t("Device directory")}>
      <FormField
        label={t("Working directory")}
        description={t(
          "Enter an absolute Device path. This is a working directory, not a filesystem sandbox.",
        )}
      >
        <Input
          value={value}
          onChange={(event) => onChange(event.target.value)}
          placeholder={info.data?.default_working_directory}
        />
      </FormField>
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={!info.data}
          onClick={() => onChange(info.data!.default_working_directory)}
        >
          {t("Use Device default")}
        </Button>
        {info.data?.directory_discovery && (
          <Button
            type="button"
            variant="outline"
            size="sm"
            onClick={() => open(value || info.data!.default_working_directory)}
          >
            <FolderIcon size={16} />
            {t("Browse directories")}
          </Button>
        )}
      </div>
      {!value && (
        <p className="text-sm text-muted-foreground">
          {t(
            "If left empty, the Device default is resolved and saved when the Run is accepted.",
          )}
        </p>
      )}
      {info.data && !info.data.directory_discovery && (
        <p className="text-sm text-muted-foreground">
          {t(
            "Browsing is disabled on this Device. Use its default or enter a known path.",
          )}
        </p>
      )}
      <ErrorNotice error={info.error} retry={() => void info.refetch()} />
      {location && (
        <div className="flex flex-col gap-2 rounded-lg bg-muted/40 p-3">
          <div className="flex flex-wrap items-center justify-between gap-2">
            <span className="min-w-0 break-all text-sm">
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
                  {t("Select this directory")}
                </Button>
              </div>
              <div className="max-h-64 overflow-y-auto">
                {page.entries?.map((entry) => (
                  <Button
                    className="w-full justify-start"
                    type="button"
                    variant="ghost"
                    key={entry.path}
                    onClick={() => open(entry.path)}
                  >
                    <FolderIcon size={16} />
                    {entry.name}
                  </Button>
                ))}
                {!page.entries?.length && (
                  <p className="text-sm text-muted-foreground">
                    {t("No subdirectories.")}
                  </p>
                )}
              </div>
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
