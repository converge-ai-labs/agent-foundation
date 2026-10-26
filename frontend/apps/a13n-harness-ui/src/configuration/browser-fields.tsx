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

  const requirements = setup?.requirements ?? [
    { name: "Browser", installed: false, detail: "", required: true },
    { name: "VNC server", installed: false, detail: "", required: true },
  ];
  return (
    <SettingsSection
      title="Browser components"
      description="Shared across all projects. Choose installed programs or select Other to enter a path. Checks preview unsaved selections; save and restart Harness UI to apply changes."
    >
      <div className={styles.configuration}>
        <div className="flex justify-end">
          <Button
            size="sm"
            variant="outline"
            disabled={scan.isFetching}
            onClick={() => void scan.refetch()}
          >
            {scan.isFetching ? "Scanning…" : "Scan"}
          </Button>
        </div>
        <ErrorNotice error={scan.error} />
        <ErrorNotice error={pending ? null : preparation.error} />
        <BrowserRequirements
          requirements={requirements}
          status={checking ? "Checking…" : !setup ? "Not checked" : undefined}
          controls={{
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
        <BrowserInstallInstructions steps={setup?.steps ?? []} />
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
        value={other ? "other" : value || undefined}
        placeholder="Select a program"
        disabled={disabled}
        hideLabel
        options={[...candidates, { value: "other", label: "Other" }]}
        onValueChange={(next) => {
          setCustom(next === "other");
          if (next !== "other") onChange(next);
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
