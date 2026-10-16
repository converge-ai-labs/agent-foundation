import { Link } from "react-router";
import { useQuery } from "@tanstack/react-query";
import { ChoiceField, SettingsRow, SettingsSection, Switch } from "a13n-ui";
import { useTransport } from "../transport/context";
import { result } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
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
      <p>
        Defaults for new conversations. Projects can override them; existing
        conversations keep their selection.
      </p>
      <SettingsSection>
        <SettingsRow
          label="Runtime"
          description="Direct runs on this Host without downloading a runtime."
        >
          <ChoiceField
            label="Runtime"
            hideLabel
            value={custom ? "custom" : direct ? "direct" : "envd"}
            options={[
              { value: "direct", label: "Direct" },
              { value: "envd", label: "envd" },
              ...(custom ? [{ value: "custom", label: "Custom profile" }] : []),
            ]}
            onValueChange={(value) => {
              if (value !== "custom")
                choose(
                  value === "direct"
                    ? "environment-native"
                    : "environment-envd",
                );
            }}
          />
        </SettingsRow>
        {!custom && (
          <SettingsRow
            label="Permissions"
            description={
              direct
                ? "Commands run as the Host account with filesystem and network access."
                : "Sandbox restricts files and processes to the selected workspace grants."
            }
          >
            {direct ? (
              <span>Full Control</span>
            ) : (
              <ChoiceField
                label="Permissions"
                hideLabel
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
            description="Allow agents using this default to observe and control the Host desktop."
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
          the profiles below.
        </p>
      ) : direct ? (
        <p className="text-sm text-muted-foreground">
          Ready to use · no envd download or desktop permissions.
        </p>
      ) : (
        <div className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-muted/30 p-4 text-sm">
          <div>
            <p>
              {ready
                ? "envd checked on this Host"
                : "Prepare envd before running with this default."}
            </p>
            <p className="text-muted-foreground">
              {!runtime.data?.computer_use && !sandbox
                ? "Desktop access is disabled in Runtime settings. "
                : ""}
              Saving this default does not start envd or request OS permissions.
            </p>
          </div>
          <Link
            className="shrink-0 font-medium underline underline-offset-4"
            to="/settings/runtime"
          >
            Open Runtime →
          </Link>
        </div>
      )}
      <ErrorNotice error={runtime.error} />
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
  const custom = configured
    ? document?.getIn(["envd", "executable"]) != null
    : runtime.data?.source === "custom";
  const update = (changes: {
    executable?: string | null;
    computer_use?: boolean;
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
      <SettingsSection>
        <SettingsRow
          label="Runtime source"
          description="Managed downloads the exact release selected by the installed client."
        >
          <ChoiceField
            label="Runtime source"
            hideLabel
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
          <div className="p-4">
            <TextField
              label="Executable path"
              value={executable}
              onChange={(value) => update({ executable: value })}
              description="Absolute path on the Harness Host to a13n-envd. Custom executables are never downloaded automatically."
            />
          </div>
        )}
        <SettingsRow
          label="Desktop capability"
          description="Permit Computer Use runtimes on this Host. Agents still need a profile that explicitly allows desktop access."
        >
          <Switch
            aria-label="Desktop capability"
            checked={computer}
            disabled={!runtime.data}
            onCheckedChange={(enabled) => update({ computer_use: enabled })}
          />
        </SettingsRow>
      </SettingsSection>
      <p className="text-sm text-muted-foreground">
        Saving does not launch a process, download a release, or request system
        permissions.
      </p>
      <ErrorNotice error={runtime.error} />
    </>
  );
}
