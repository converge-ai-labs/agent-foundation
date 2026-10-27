import { useEffect, useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, ChoiceField, SettingsSection } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import {
  BrowserRequirements,
  BrowserInstallInstructions,
} from "../browser/requirements";
import { MacCaptureSettings } from "../browser/macos-setup";
import { readDocument, updateDocument } from "./documents";
import styles from "../browser/setup.module.css";

export function BrowserFields({
  source,
  onChange,
  active = true,
}: {
  source: string;
  onChange: (source: string) => void;
  active?: boolean;
}) {
  const { client } = useTransport();
  const scan = useQuery({
    queryKey: ["browser-executables"],
    queryFn: () => result(client.POST("/api/browser/scan")),
    enabled: active,
    staleTime: 0,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  });
  const document = readDocument(source);
  const text = (key: string) => String(document?.getIn(["browser", key]) ?? "");
  const set = (key: string, value: string) =>
    onChange(updateDocument(source, ["browser", key], value || null));
  const executable = text("executable") || null;
  const vncExecutable = text("vnc_executable") || null;
  const [selection, setSelection] = useState({
    executable,
    vnc_executable: vncExecutable,
  });
  useEffect(() => {
    const timer = setTimeout(
      () => setSelection({ executable, vnc_executable: vncExecutable }),
      350,
    );
    return () => clearTimeout(timer);
  }, [executable, vncExecutable]);
  const pending =
    selection.executable !== executable ||
    selection.vnc_executable !== vncExecutable;
  const preparation = useQuery({
    queryKey: ["browser-requirements", selection, scan.dataUpdatedAt],
    queryFn: ({ signal }) =>
      result(client.POST("/api/browser/check", { body: selection, signal })),
    enabled: active && !!scan.data && !scan.isFetching && !pending,
    refetchOnWindowFocus: false,
    refetchOnReconnect: false,
    retry: false,
  });
  const checking = pending || scan.isFetching || preparation.isFetching;
  const setup = !pending && !scan.isFetching ? preparation.data : undefined;
  const disabled = !scan.data || scan.isFetching;
  const notice = checking
    ? "Checking selected components…"
    : !setup
      ? "Scan installed programs to check browser requirements."
      : !setup.supported
        ? setup.message
        : !setup.ready
          ? "Required components are missing. Review the checks above before starting the browser."
          : null;

  const mac = scan.data?.platform === "macos";
  const requirements = setup?.requirements ?? [
    { name: "Browser", installed: false, detail: "", required: true },
    {
      name: mac ? "Window preview" : "VNC server",
      installed: false,
      detail: "",
      required: !mac,
    },
  ];
  return (
    <SettingsSection
      title={
        <span className="flex items-center justify-between gap-4">
          <span>Advanced browser settings</span>
          {setup?.supported !== false && (
            <Button
              size="sm"
              variant="outline"
              disabled={scan.isFetching}
              onClick={() => void scan.refetch()}
            >
              {scan.isFetching ? "Scanning…" : "Scan"}
            </Button>
          )}
        </span>
      }
      description="Installed components are selected automatically. Executable overrides require saving and restarting Harness UI."
    >
      <div className={styles.configuration}>
        {setup?.supported === false ? (
          <p className="text-xs text-muted-foreground" role="status">
            {setup.message}
          </p>
        ) : (
          <>
            <ErrorNotice error={scan.error} />
            <ErrorNotice error={pending ? null : preparation.error} />
            <BrowserRequirements
              requirements={requirements}
              status={
                checking ? "Checking…" : !setup ? "Not checked" : undefined
              }
              controls={{
                "Window preview": mac ? <MacCaptureSettings /> : undefined,
                Browser: (
                  <ExecutableField
                    label="Browser executable"
                    value={text("executable")}
                    candidates={(scan.data?.browsers ?? []).map((item) => ({
                      value: item.path,
                      label: `${item.name} — ${item.path}`,
                    }))}
                    disabled={disabled}
                    onChange={(value) => set("executable", value)}
                  />
                ),
                "VNC server": (
                  <ExecutableField
                    label="VNC executable"
                    value={text("vnc_executable")}
                    candidates={(scan.data?.vncs ?? []).map((item) => ({
                      value: item.path,
                      label: `${item.kind === "x11vnc" ? "x11vnc" : item.kind === "wayvnc" ? "WayVNC" : "TigerVNC Xvnc"} — ${item.path}`,
                    }))}
                    disabled={disabled}
                    onChange={(value) => set("vnc_executable", value)}
                  />
                ),
              }}
            />
            {notice && (
              <p className="text-xs text-muted-foreground" role="status">
                {notice}
              </p>
            )}
            {setup?.vnc && setup.vnc.kind !== "tigervnc" && (
              <p className="text-xs text-muted-foreground">
                This VNC adapter uses a fixed desktop size. Zoom is available;
                Viewport size switching is unavailable.
              </p>
            )}
            <p className="text-xs text-muted-foreground">
              {mac
                ? ""
                : "Missing a program? Install a Chromium-based browser and VNC server on this Host (inside the container for Docker), then Scan again. Use Other for an installed program outside the detected locations. "}
              <a
                href="https://a13n.dev/docs/a13n-harness-ui/webui#first-time-setup"
                target="_blank"
                rel="noreferrer"
                className="underline underline-offset-4"
              >
                Browser setup guide
              </a>
            </p>
            <BrowserInstallInstructions steps={setup?.steps ?? []} />
          </>
        )}
      </div>
    </SettingsSection>
  );
}

function ExecutableField({
  label,
  value,
  candidates,
  disabled,
  onChange,
}: {
  label: string;
  value: string;
  candidates: { value: string; label: string }[];
  disabled: boolean;
  onChange: (value: string) => void;
}) {
  const [custom, setCustom] = useState(false);
  const other =
    custom || (!!value && !candidates.some((item) => item.value === value));
  return (
    <div className="flex min-w-0 flex-col gap-3">
      <ChoiceField
        label={label}
        value={other ? "other" : value || "automatic"}
        disabled={disabled}
        hideLabel
        options={[
          { value: "automatic", label: "Automatic" },
          ...candidates,
          { value: "other", label: "Other" },
        ]}
        onValueChange={(next) => {
          setCustom(next === "other");
          if (next !== "other") onChange(next === "automatic" ? "" : next);
        }}
      />
      {other && (
        <TextField
          label={`${label} path`}
          value={value}
          disabled={disabled}
          onChange={onChange}
          description="Absolute path on this Host. Invalid paths are reported without falling back to another program."
        />
      )}
    </div>
  );
}
