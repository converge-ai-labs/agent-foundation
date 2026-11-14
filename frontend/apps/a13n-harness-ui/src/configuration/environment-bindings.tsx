import { useTranslation } from "../i18n";
import {
  SettingsTextField as TextField,
  SettingsChoice as ChoiceField,
  SettingsSection,
} from "../shell/settings";
import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, ModalFrame } from "a13n-ui";
import { PlusIcon } from "@phosphor-icons/react";
import styles from "./environments.module.css";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import { DeviceDirectory, useDeviceInfo } from "./device-directory";
import { ConnectDevice } from "./connect-device";
import {
  fullControl,
  readOnly,
  permissionLabel,
  permissionPreset,
} from "./environment-permissions";
import {
  deviceEnvironments,
  directoryCount,
  replaceDeviceEnvironment,
  selectedWorkingDirectory,
  workingDirectories,
  type DeviceEnvironment,
  type EnvironmentBinding,
  type EnvironmentSelection,
} from "./environment-selection";

export type { EnvironmentBinding } from "./environment-selection";

function useDevices(enabled = true) {
  const { client } = useTransport();
  return useQuery({
    queryKey: ["devices"],
    queryFn: ({ signal }) => result(client.GET("/api/devices", { signal })),
    enabled,
  });
}

export function DefaultWorkingDirectory({
  value,
  allowUnsetDefault = false,
  onChange,
}: {
  value: EnvironmentSelection;
  allowUnsetDefault?: boolean;
  onChange: (alias: string | null) => void;
}) {
  const { t } = useTranslation();
  const devices = useDevices(!!value.environment_bindings?.length);
  const choices = workingDirectories(value, devices.data);
  const selected =
    value.default_environment ??
    (allowUnsetDefault ? "" : selectedWorkingDirectory(value));
  const missing =
    !!selected && !choices.some((item) => item.value === selected);
  if (choices.length === 1 && !missing && !allowUnsetDefault) return null;
  return (
    <SettingsSection className={styles.defaultDirectory}>
      <ChoiceField
        label={t("Default folder")}
        popupClassName={styles.directoryOptions}
        value={selected}
        onValueChange={(alias) => onChange(alias || null)}
        options={[
          ...(allowUnsetDefault && !value.environment_bindings?.length
            ? [
                {
                  value: "",
                  label: t("Use chat default"),
                },
              ]
            : []),
          ...(missing
            ? [
                {
                  value: selected,
                  label: t("Removed directory - Choose a replacement"),
                  disabled: true,
                },
              ]
            : []),
          ...choices,
        ]}
      />
      {missing && (
        <p role="alert">{t("Choose a default folder before saving.")}</p>
      )}
    </SettingsSection>
  );
}

export function BindingSummary({
  bindings,
  defaultEnvironment,
}: {
  bindings: readonly EnvironmentBinding[];
  defaultEnvironment?: string | null;
}) {
  const { t } = useTranslation();
  const devices = useDevices(bindings.length > 0);
  return (
    <>
      {deviceEnvironments(bindings).map((group) => (
        <section key={group.key} className={styles.summaryEnvironment}>
          <p className="font-medium">
            {devices.data?.find((item) => item.id === group.deviceId)?.name ??
              group.deviceId}
            {" - "}
            {permissionLabel(group.directories[0].permission_ceiling)}
          </p>
          <p className="text-muted-foreground">
            {directoryCount(group.directories.length)}
          </p>
          {group.directories.map((binding) => (
            <p key={binding.alias} className={styles.summaryPath}>
              {binding.working_directory}
              {binding.alias === defaultEnvironment && (
                <span className="ml-2 text-muted-foreground">
                  {t("Default")}
                </span>
              )}
            </p>
          ))}
        </section>
      ))}
    </>
  );
}

type EditingEnvironment = { group?: DeviceEnvironment; addDirectory?: boolean };

export function EnvironmentBindings({
  bindings,
  defaultEnvironment,
  localRoots,
  allowEmpty = true,
  onChange,
}: {
  bindings: EnvironmentBinding[];
  defaultEnvironment?: string | null;
  localRoots: readonly string[];
  allowEmpty?: boolean;
  onChange: (
    bindings: EnvironmentBinding[],
    defaultEnvironment: string | null,
  ) => void;
}) {
  const { t } = useTranslation();
  const devices = useDevices();
  const [editing, setEditing] = useState<EditingEnvironment>();
  const [removing, setRemoving] = useState<EnvironmentBinding>();
  const [replacement, setReplacement] = useState("");
  const value = {
    local_roots: [...localRoots],
    environment_bindings: bindings,
    default_environment: defaultEnvironment,
  };
  const choices = workingDirectories(value, devices.data);
  const selectedDefault = selectedWorkingDirectory(value);
  const removingDefault = removing?.alias === selectedDefault;
  return (
    <section className={styles.remote} aria-label={t("Remote environments")}>
      <ErrorNotice error={devices.error} />
      {deviceEnvironments(bindings).map((group) => {
        const name =
          devices.data?.find((item) => item.id === group.deviceId)?.name ??
          group.deviceId;
        const policy = group.directories[0];
        return (
          <section
            key={group.key}
            className={styles.environment}
            aria-label={t("{{name}} environment", { name })}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h3 className="font-medium">{name}</h3>
                <p className="text-sm text-muted-foreground">
                  {permissionLabel(policy.permission_ceiling)} -{" "}
                  {directoryCount(group.directories.length)}
                </p>
                {devices.data &&
                  !devices.data.some((item) => item.id === group.deviceId) && (
                    <p className="text-sm">{t("Device not configured")}</p>
                  )}
              </div>
              <div className="flex flex-wrap gap-1">
                <BindingStatus deviceId={group.deviceId} />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditing({ group })}
                  aria-label={t("Edit {{name}} environment", { name })}
                >
                  {t("Edit")}
                </Button>
              </div>
            </div>
            <ul className={styles.directoryList}>
              {group.directories.map((binding) => (
                <li key={binding.alias} className={styles.directory}>
                  <div className="min-w-0 text-sm">
                    <p className="break-all">{binding.working_directory}</p>
                    {binding.alias === selectedDefault && (
                      <span className="text-xs text-muted-foreground">
                        {t("Default folder")}
                      </span>
                    )}
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={t("Remove directory {{path}}", {
                      path: binding.working_directory,
                    })}
                    disabled={!allowEmpty && bindings.length === 1}
                    onClick={() => {
                      setRemoving(binding);
                      setReplacement("");
                    }}
                  >
                    {t("Remove")}
                  </Button>
                </li>
              ))}
            </ul>
            <div>
              <Button
                type="button"
                size="sm"
                variant="ghost"
                disabled={bindings.length >= 64}
                aria-label={t("Add directory to {{name}}", { name })}
                onClick={() => setEditing({ group, addDirectory: true })}
              >
                <PlusIcon />
                {t("Add directory")}
              </Button>
            </div>
            <details className={styles.advanced}>
              <summary className="cursor-pointer">{t("Run details")}</summary>
              <p className="mt-2">
                {policy.permission_ceiling?.operations?.join(", ") ??
                  t("Files and execution")}
              </p>
              {(Object.keys(policy.session ?? {}).length > 0 ||
                policy.expected_boundary) && (
                <p>{t("Custom network and access rules are kept.")}</p>
              )}
              {group.directories.map((binding) => (
                <p key={binding.alias} className="mt-1 break-all">
                  {binding.alias} - {binding.working_directory}
                </p>
              ))}
            </details>
          </section>
        );
      })}

      <div className={styles.remoteHeading}>
        <Button
          type="button"
          variant="outline"
          size="sm"
          disabled={bindings.length >= 64}
          onClick={() => setEditing({})}
        >
          <PlusIcon />
          {t("Add remote environment")}
        </Button>
      </div>
      <ModalFrame
        bottomStickOnMobile
        open={editing !== undefined}
        onOpenChange={(open) => {
          if (!open) setEditing(undefined);
        }}
        title={editing?.group ? t("Edit environment") : t("Add environment")}
        closeLabel={t("Close")}
      >
        {editing && (
          <DeviceEnvironmentEditor
            initial={editing.group?.directories ?? []}
            addDirectory={editing.addDirectory}
            devices={devices.data ?? []}
            otherBindings={bindings.filter(
              (binding) => !editing.group?.directories.includes(binding),
            )}
            save={(next) => {
              const patch = replaceDeviceEnvironment(
                bindings,
                editing.group?.directories ?? [],
                next,
                selectedDefault,
              );
              onChange(patch.environment_bindings, patch.default_environment);
              setEditing(undefined);
            }}
            cancel={() => setEditing(undefined)}
          />
        )}
      </ModalFrame>
      <ModalFrame
        bottomStickOnMobile
        open={!!removing}
        onOpenChange={(open) => {
          if (!open) setRemoving(undefined);
        }}
        title={t("Remove directory selection?")}
        description={t(
          "Remove this folder from the selection? Files, the device connection and run history are kept.",
        )}
        closeLabel={t("Close")}
        footer={
          <>
            <Button
              type="button"
              variant="outline"
              onClick={() => setRemoving(undefined)}
            >
              {t("Cancel")}
            </Button>
            <Button
              type="button"
              disabled={removingDefault && !replacement}
              onClick={() => {
                onChange(
                  bindings.filter((item) => item.alias !== removing?.alias),
                  removingDefault ? replacement : selectedDefault,
                );
                setRemoving(undefined);
              }}
            >
              {t("Remove selection")}
            </Button>
          </>
        }
      >
        <p className="break-all">{removing?.working_directory}</p>
        {removingDefault && (
          <ChoiceField
            label={t("Replacement default")}
            placeholder={t("Choose a directory")}
            value={replacement}
            onValueChange={setReplacement}
            options={choices.filter((item) => item.value !== removing?.alias)}
          />
        )}
      </ModalFrame>
    </section>
  );
}

function DeviceEnvironmentEditor({
  initial,
  addDirectory = false,
  devices,
  otherBindings,
  save,
  cancel,
}: {
  initial: EnvironmentBinding[];
  addDirectory?: boolean;
  devices: Schema<"DeviceSummary">[];
  otherBindings: EnvironmentBinding[];
  save: (bindings: EnvironmentBinding[]) => void;
  cancel: () => void;
}) {
  const { t } = useTranslation();
  const first = initial[0];
  const [deviceId, setDeviceId] = useState(first?.device_id ?? "");
  const [directories, setDirectories] = useState(() => [
    ...initial.map((item) => ({
      path: item.working_directory,
      alias: item.alias,
    })),
    ...(!initial.length || addDirectory ? [{ path: "", alias: "" }] : []),
  ]);
  const [permission, setPermission] = useState<string | undefined>(
    first ? permissionPreset(first.permission_ceiling) : "full",
  );
  const [invalidAlias, setInvalidAlias] = useState(false);
  const [connected, setConnected] = useState<Schema<"DeviceSummary">>();
  const availableDevices =
    connected && !devices.some((item) => item.id === connected.id)
      ? [...devices, connected]
      : devices;
  const name =
    availableDevices.find((item) => item.id === deviceId)?.name ?? deviceId;
  const selectDevice = (id: string) => {
    setDeviceId(id);
    // Existing directory references are retained; changing a Device is an explicit group edit.
    if (!first) setDirectories([{ path: "", alias: "" }]);
  };
  const updateDirectory = (
    index: number,
    patch: Partial<(typeof directories)[number]>,
  ) =>
    setDirectories(
      directories.map((item, i) =>
        i === index ? { ...item, ...patch } : item,
      ),
    );
  return (
    <div className={styles.editor}>
      <SettingsSection>
        <ChoiceField
          label={t("Device")}
          placeholder={
            availableDevices.length
              ? t("Choose a device")
              : t("No devices connected")
          }
          disabled={!availableDevices.length && !deviceId}
          description={
            !availableDevices.length && !deviceId
              ? t("Connect a device to add a remote environment.")
              : undefined
          }
          value={deviceId}
          onValueChange={selectDevice}
          options={[
            ...(deviceId &&
            !availableDevices.some((item) => item.id === deviceId)
              ? [
                  {
                    value: deviceId,
                    label: t("{{name}} - Not configured", { name: deviceId }),
                  },
                ]
              : []),
            ...availableDevices.map((item) => ({
              value: item.id,
              label: item.name,
            })),
          ]}
        />
        {deviceId && (
          <ChoiceField
            label={t("Allowed actions")}
            value={permission}
            onValueChange={setPermission}
            placeholder={t("Keep existing permissions")}
            options={[
              { value: "read_only", label: t("Read only") },
              { value: "full", label: t("Full control") },
            ]}
            description={
              permission === "full"
                ? t(
                    "Applies to the folders below. Allows files, commands and any enabled desktop controls. Desktop access extends beyond these folders. Screenshots may be sent to the model and saved in chat history.",
                  )
                : permission === "read_only"
                  ? t(
                      "Read files in the folders below. No file changes, commands or desktop access.",
                    )
                  : t(
                      "Keep each folder's current permissions unless you choose an option.",
                    )
            }
          />
        )}
      </SettingsSection>
      <div>
        <ConnectDevice
          label={t("Connect new device")}
          onConnected={(device) => {
            setConnected(device);
            selectDevice(device.id);
          }}
        />
      </div>
      {deviceId &&
        directories.map((directory, index) => (
          <div key={`${deviceId}:${index}`} className={styles.draftDirectory}>
            <DeviceDirectory
              deviceId={deviceId}
              value={directory.path}
              onChange={(path) => updateDirectory(index, { path })}
              label={
                index === 0
                  ? t("Working directory")
                  : t("Working directory {{number}}", { number: index + 1 })
              }
            />
            <details className={styles.advanced}>
              <summary>{t("Advanced routing")}</summary>
              <TextField
                label={
                  index === 0
                    ? t("Directory alias")
                    : t("Directory alias {{number}}", { number: index + 1 })
                }
                value={directory.alias}
                onChange={(alias) => updateDirectory(index, { alias })}
                description={t(
                  "An internal name for this connection. Leave blank to generate one.",
                )}
              />
            </details>
            {index >= initial.length &&
              directories.length > Math.max(initial.length, 1) && (
                <Button
                  type="button"
                  variant="ghost"
                  size="sm"
                  onClick={() =>
                    setDirectories(directories.filter((_, i) => i !== index))
                  }
                >
                  {t("Discard directory")}
                </Button>
              )}
          </div>
        ))}
      {deviceId && (
        <div>
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={directories.length + otherBindings.length >= 64}
            onClick={() =>
              setDirectories([...directories, { path: "", alias: "" }])
            }
          >
            <PlusIcon />
            {t("Add folder")}
          </Button>
        </div>
      )}
      <ErrorNotice
        error={
          invalidAlias
            ? new Error(
                t(
                  "Choose unique directory aliases using lowercase letters, numbers, hyphens or underscores. Host mount names are reserved.",
                ),
              )
            : undefined
        }
      />
      <div data-a13n-form-actions className={styles.actions}>
        <Button type="button" variant="outline" onClick={cancel}>
          {t("Cancel")}
        </Button>
        <Button
          type="button"
          disabled={!deviceId || directories.some((item) => !item.path.trim())}
          onClick={() => {
            const aliases = [...otherBindings.map((item) => item.alias)];
            const next: EnvironmentBinding[] = [];
            for (const [index, directory] of directories.entries()) {
              const alias =
                directory.alias ||
                suggestAlias(name, [
                  ...aliases,
                  ...directories.map((item) => item.alias),
                ]);
              if (
                aliases.includes(alias) ||
                reservedAlias(alias) ||
                !/^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$/.test(alias) ||
                alias.length > 63
              ) {
                setInvalidAlias(true);
                return;
              }
              aliases.push(alias);
              const existing = initial[index] ?? first;
              next.push({
                ...existing,
                device_id: deviceId,
                alias,
                working_directory: directory.path,
                permission_ceiling:
                  permission === permissionPreset(existing?.permission_ceiling)
                    ? existing?.permission_ceiling
                    : permission === "read_only"
                      ? { operations: readOnly }
                      : permission === "full"
                        ? { operations: fullControl }
                        : existing?.permission_ceiling,
              });
            }
            save(next);
          }}
        >
          {t("Use environment")}
        </Button>
      </div>
    </div>
  );
}

function reservedAlias(alias: string) {
  return (
    [
      "workspace",
      "thread-files",
      "configuration",
      "builtin-skills",
      "user-skills",
    ].includes(alias) || /^(workspace|content-plugin)-[0-9]+$/.test(alias)
  );
}

export function suggestAlias(name: string, used: readonly string[]) {
  const base =
    name
      .toLowerCase()
      .replace(/[^a-z0-9]+/g, "-")
      .replace(/^-+|-+$/g, "")
      .replace(/^[^a-z]+/, "")
      .slice(0, 50)
      .replace(/-+$/, "") || "device";
  let alias = base;
  for (let suffix = 2; reservedAlias(alias) || used.includes(alias); suffix++)
    alias = `${base}-${suffix}`;
  return alias;
}

function BindingStatus({ deviceId }: { deviceId: string }) {
  const { t } = useTranslation();
  const info = useDeviceInfo(deviceId, false);
  return (
    <Button
      type="button"
      size="sm"
      variant="ghost"
      loading={info.isFetching}
      onClick={() => void info.refetch()}
    >
      {info.data
        ? info.data.available
          ? t("Online - Check")
          : t("Offline - Retry")
        : t("Check connection")}
    </Button>
  );
}
