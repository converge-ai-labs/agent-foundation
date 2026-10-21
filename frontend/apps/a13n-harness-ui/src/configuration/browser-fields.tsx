import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, ChoiceField, SettingsSection } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";
import styles from "../shell/workbench.module.css";

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
  const disabled = !scan.data || scan.isFetching;
  return (
    <SettingsSection
      title="Executable paths"
      description="These Host settings apply to every project. Installed programs are scanned when you open this section. Choose a program, or select Other to specify a path. Save and restart Harness UI to apply changes."
    >
      <div className={`${styles.fieldGroup} ${styles.stack}`}>
        <div className="flex flex-wrap items-center gap-3">
          <Button
            variant="outline"
            disabled={scan.isFetching}
            onClick={() => void scan.refetch()}
          >
            {scan.isFetching ? "Scanning…" : "Scan"}
          </Button>
          <span className="text-sm text-muted-foreground" role="status">
            {scan.isFetching
              ? "Checking installed executables…"
              : scan.data
                ? `Browsers: ${scan.data.browsers?.length ?? 0} · VNC servers: ${scan.data.vncs?.length ?? 0}`
                : "Click Scan to retry executable discovery."}
          </span>
        </div>
        <ErrorNotice error={scan.error} />
        <ExecutableField
          label="Browser executable"
          value={text("executable")}
          candidates={(scan.data?.browsers ?? []).map((item) => ({
            value: item.path,
            label: `${item.name} — ${item.path}`,
          }))}
          disabled={disabled}
          onChange={(value) => set("executable", value)}
          description="Choose the Chrome, Chromium or Edge executable that Harness should always launch."
        />
        <ExecutableField
          label="VNC executable"
          value={text("vnc_executable")}
          candidates={(scan.data?.vncs ?? []).map((item) => ({
            value: item.path,
            label: `${item.kind === "x11vnc" ? "x11vnc" : item.kind === "wayvnc" ? "WayVNC" : "TigerVNC Xvnc"} — ${item.path}`,
          }))}
          disabled={disabled}
          onChange={(value) => set("vnc_executable", value)}
          description="TigerVNC Xvnc, x11vnc with Xvfb, or WayVNC with headless Sway. Harness manages its own graphical session."
        />
        <TextField
          label="Shared profile directory"
          value={text("profile_root")}
          onChange={(value) => set("profile_root", value)}
          description="Shared by all conversations and projects. Defaults to browsers/profile under the Harness UI data directory. Changing this path does not move existing profiles."
        />
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
  description,
}: {
  label: string;
  value: string;
  candidates: { value: string; label: string }[];
  disabled: boolean;
  onChange: (value: string) => void;
  description: string;
}) {
  const [custom, setCustom] = useState(false);
  const other =
    custom || (!!value && !candidates.some((item) => item.value === value));
  return (
    <>
      <ChoiceField
        label={label}
        value={other ? "other" : value || undefined}
        placeholder="Select a program"
        disabled={disabled}
        description={description}
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
    </>
  );
}
