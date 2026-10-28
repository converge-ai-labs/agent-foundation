import styles from "../shell/workbench.module.css";
import { SettingsTextField as TextField } from "../shell/settings";
import { SettingsChoice as ChoiceField } from "../shell/settings";
import { SettingsRow, SettingsSection } from "../shell/settings";
import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { Switch } from "a13n-ui";
import { useSelectors, useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { readDocument, updateDocument } from "./documents";

type FieldsProps = { source: string; onChange: (source: string) => void };

export function useEnvdStatus() {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["envd-runtime"],
    queryFn: ({ signal }) => result(client.GET("/api/envd", { signal })),
    refetchInterval: (query) =>
      query.state.data?.preparing || query.state.data?.phase !== "idle"
        ? 1000
        : 5000,
  });
}

export function ExecutionDefaultsFields({ source, onChange }: FieldsProps) {
  const runtime = useEnvdStatus();
  const selectors = useSelectors();
  const profiles =
    selectors.data?.environments.filter((item) => !item.release_owned) ?? [];
  const selected = String(
    readDocument(source)?.getIn(["defaults", "environment_profile"]) ??
      "environment-native",
  );
  const direct = selected === "environment-native";
  const sandbox = selected === "environment-sandbox";
  const desktop = selected === "environment-desktop";
  const custom = ![
    "environment-native",
    "environment-envd",
    "environment-desktop",
    "environment-sandbox",
  ].includes(selected);
  const choose = (profile: string) =>
    onChange(
      updateDocument(source, ["defaults", "environment_profile"], profile),
    );
  const ready = runtime.data?.readiness?.find(
    (item) => item.profile_id === selected,
  )?.ready;
  return (
    <>
      <SettingsSection variant="grouped">
        <SettingsRow
          label="Runtime"
          description="Direct runs on this host without downloading a runtime."
        >
          <ChoiceField
            label="Runtime"
            hideLabel
            className={styles.settingControl}
            value={custom ? selected : direct ? "direct" : "envd"}
            options={[
              { value: "direct", label: "Direct" },
              { value: "envd", label: "envd" },
              ...profiles.map((item) => ({
                value: item.profile_id,
                label: item.name,
              })),
              ...(custom &&
              !profiles.some((item) => item.profile_id === selected)
                ? [{ value: selected, label: selected }]
                : []),
            ]}
            onValueChange={(value) => {
              choose(
                value === "direct"
                  ? "environment-native"
                  : value === "envd"
                    ? "environment-envd"
                    : value,
              );
            }}
          />
        </SettingsRow>
        {!custom && (
          <SettingsRow
            label="Permissions"
            description={
              direct
                ? "Commands use the server's account, with file and network access."
                : "Sandbox limits file and process access to the allowed workspace."
            }
          >
            {direct ? (
              <span>Full Control</span>
            ) : (
              <ChoiceField
                label="Permissions"
                hideLabel
                className={styles.settingControl}
                value={sandbox ? "sandbox" : "full-control"}
                options={[
                  { value: "full-control", label: "Full Control" },
                  { value: "sandbox", label: "Sandbox" },
                ]}
                onValueChange={(value) =>
                  choose(
                    value === "sandbox"
                      ? "environment-sandbox"
                      : "environment-envd",
                  )
                }
              />
            )}
          </SettingsRow>
        )}
        {!custom && !direct && !sandbox && (
          <SettingsRow
            label="Computer Use"
            description="Let agents using this default see and control this computer's desktop."
          >
            <Switch
              aria-label="Computer Use"
              checked={desktop}
              disabled={!desktop && !runtime.data?.computer_use}
              onCheckedChange={(enabled) =>
                choose(enabled ? "environment-desktop" : "environment-envd")
              }
            />
          </SettingsRow>
        )}
      </SettingsSection>
      {custom ? (
        <p>
          Custom profile: <code>{selected}</code>. Change its configuration in
          Profiles & extensions.
        </p>
      ) : direct ? null : (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-muted/30 p-4 text-sm">
          <div>
            <p>
              {ready
                ? "envd checked on this host"
                : "Prepare envd before running with this default."}
            </p>
            <p className="text-muted-foreground">
              {!runtime.data?.computer_use && !sandbox
                ? "Desktop access is disabled in Runtime settings. "
                : ""}
            </p>
          </div>
          <Link
            className="shrink-0 font-medium underline underline-offset-4"
            to="/settings/environments?tab=runtime"
          >
            Manage local runtime →
          </Link>
        </div>
      )}
      <ErrorNotice error={runtime.error || selectors.error} />
    </>
  );
}

export function EnvdConfigurationFields({ source, onChange }: FieldsProps) {
  const runtime = useEnvdStatus();
  const document = readDocument(source);
  const configured = document?.get("envd") != null;
  const executable = configured
    ? String(document?.getIn(["envd", "executable"]) ?? "")
    : (runtime.data?.configured_executable ?? "");
  const computer = configured
    ? document?.getIn(["envd", "computer_use"]) === true
    : (runtime.data?.computer_use ?? false);
  const interval = Number(
    document?.getIn(["envd", "monitoring_interval_seconds"]) ?? 60,
  );
  const custom = configured
    ? document?.getIn(["envd", "executable"]) != null
    : runtime.data?.source === "custom";
  const update = (changes: {
    executable?: string | null;
    computer_use?: boolean;
    monitoring_interval_seconds?: number;
  }) => {
    let next = source;
    if (!configured)
      next = updateDocument(next, ["envd"], {
        executable: custom ? executable : null,
        computer_use: computer,
      });
    for (const [key, value] of Object.entries(changes))
      next = updateDocument(next, ["envd", key], value);
    onChange(next);
  };
  return (
    <>
      <SettingsSection variant="grouped">
        <SettingsRow
          label="Runtime source"
          description="Automatically downloads the envd version required by Harness."
        >
          <ChoiceField
            label="Runtime source"
            hideLabel
            className={styles.settingControl}
            disabled={!runtime.data}
            value={custom ? "custom" : "managed"}
            options={[
              { value: "managed", label: "Managed release" },
              { value: "custom", label: "Custom executable" },
            ]}
            onValueChange={(value) =>
              update({
                executable:
                  value === "managed" ? null : (runtime.data?.executable ?? ""),
              })
            }
          />
        </SettingsRow>
        {custom && (
          <TextField
            label="Executable path"
            value={executable}
            onChange={(value) => update({ executable: value })}
            description="Full path to a13n-envd on this server. You must install and update it yourself."
          />
        )}
        <SettingsRow
          label="Metrics collection"
          description="How often Harness collects metrics. This also sets sampling for its local envd runtime. Other runtimes keep their own interval. Restart Harness UI to apply."
        >
          <ChoiceField
            label="Metrics collection interval"
            hideLabel
            className={styles.settingControl}
            disabled={!runtime.data}
            value={String(interval)}
            options={[
              { value: "10", label: "Every 10 seconds" },
              { value: "30", label: "Every 30 seconds" },
              { value: "60", label: "Every minute" },
              ...([10, 30, 60].includes(interval)
                ? []
                : [
                    {
                      value: String(interval),
                      label: `Every ${interval} seconds`,
                    },
                  ]),
            ]}
            onValueChange={(value) =>
              update({ monitoring_interval_seconds: Number(value) })
            }
          />
        </SettingsRow>
        <SettingsRow
          label="Desktop capability"
          description="Allow desktop controls on this server. Each agent still needs a profile with desktop access."
        >
          <Switch
            aria-label="Desktop capability"
            checked={computer}
            disabled={!runtime.data}
            onCheckedChange={(enabled) => update({ computer_use: enabled })}
          />
        </SettingsRow>
      </SettingsSection>
      <ErrorNotice error={runtime.error} />
    </>
  );
}
