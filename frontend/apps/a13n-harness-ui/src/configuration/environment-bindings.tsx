import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { PlusIcon } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
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
  const devices = useDevices(!!value.environment_bindings?.length);
  const choices = workingDirectories(value, devices.data);
  const selected =
    value.default_environment ??
    (allowUnsetDefault ? "" : selectedWorkingDirectory(value));
  const missing =
    !!selected && !choices.some((item) => item.value === selected);
  return (
    <div className="flex flex-col gap-2">
      <ChoiceField
        label="Default working directory"
        value={selected}
        onValueChange={(alias) => onChange(alias || null)}
        description="Used for relative paths and commands without an explicit directory."
        options={[
          ...(allowUnsetDefault && !value.environment_bindings?.length
            ? [
                {
                  value: "",
                  label:
                    "Keep Thread default / automatic for new conversations",
                },
              ]
            : []),
          ...(missing
            ? [
                {
                  value: selected,
                  label: "Removed directory · Choose a replacement",
                  disabled: true,
                },
              ]
            : []),
          ...choices,
        ]}
      />
      {missing && (
        <p role="alert">
          Choose a default from the selected directories before saving.
        </p>
      )}
    </div>
  );
}

export function BindingSummary({
  bindings,
  defaultEnvironment,
}: {
  bindings: readonly EnvironmentBinding[];
  defaultEnvironment?: string | null;
}) {
  const devices = useDevices(bindings.length > 0);
  return (
    <>
      {deviceEnvironments(bindings).map((group) => (
        <section
          key={group.key}
          className="flex flex-col gap-2 rounded-lg bg-muted/40 p-3"
        >
          <p className="font-medium">
            {devices.data?.find((item) => item.id === group.deviceId)?.name ??
              group.deviceId}
            {" · "}
            {permissionLabel(group.directories[0].permission_ceiling)}
          </p>
          <p className="text-muted-foreground">
            {directoryCount(group.directories.length)}
          </p>
          {group.directories.map((binding) => (
            <p key={binding.alias} className="break-all">
              {binding.working_directory}
              {binding.alias === defaultEnvironment && (
                <span className="ml-2 text-muted-foreground">Default</span>
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
    <section className="flex flex-col gap-4" aria-label="Remote environments">
      <ErrorNotice error={devices.error} />
      {deviceEnvironments(bindings).map((group) => {
        const name =
          devices.data?.find((item) => item.id === group.deviceId)?.name ??
          group.deviceId;
        const policy = group.directories[0];
        return (
          <section
            key={group.key}
            className="flex min-w-0 flex-col gap-3 rounded-lg bg-muted/40 p-4"
            aria-label={`${name} environment`}
          >
            <div className="flex flex-wrap items-start justify-between gap-2">
              <div>
                <h3 className="font-medium">{name}</h3>
                <p className="text-sm text-muted-foreground">
                  {permissionLabel(policy.permission_ceiling)} ·{" "}
                  {directoryCount(group.directories.length)}
                </p>
                {devices.data &&
                  !devices.data.some((item) => item.id === group.deviceId) && (
                    <p className="text-sm">Device not configured</p>
                  )}
              </div>
              <div className="flex flex-wrap gap-1">
                <BindingStatus deviceId={group.deviceId} />
                <Button
                  type="button"
                  size="sm"
                  variant="ghost"
                  onClick={() => setEditing({ group })}
                  aria-label={`Edit ${name} environment`}
                >
                  Edit
                </Button>
              </div>
            </div>
            <ul className="flex min-w-0 flex-col gap-2">
              {group.directories.map((binding) => (
                <li
                  key={binding.alias}
                  className="flex items-center justify-between gap-3"
                >
                  <div className="min-w-0 text-sm">
                    <p className="break-all">{binding.working_directory}</p>
                    {binding.alias === selectedDefault && (
                      <span className="text-xs text-muted-foreground">
                        Default working directory
                      </span>
                    )}
                  </div>
                  <Button
                    type="button"
                    size="sm"
                    variant="ghost"
                    aria-label={`Remove directory ${binding.working_directory}`}
                    disabled={!allowEmpty && bindings.length === 1}
                    onClick={() => {
                      setRemoving(binding);
                      setReplacement("");
                    }}
                  >
                    Remove
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
                aria-label={`Add directory to ${name}`}
                onClick={() => setEditing({ group, addDirectory: true })}
              >
                <PlusIcon />
                Add directory
              </Button>
            </div>
            <details className="text-xs text-muted-foreground">
              <summary className="cursor-pointer">Execution details</summary>
              <p className="mt-2">
                {policy.permission_ceiling?.operations?.join(", ") ??
                  "Files and execution"}
              </p>
              {(policy.egress || policy.expected_boundary) && (
                <p>Custom network or boundary requirements are retained.</p>
              )}
              {group.directories.map((binding) => (
                <p key={binding.alias} className="mt-1 break-all">
                  {binding.alias} · {binding.working_directory}
                </p>
              ))}
            </details>
          </section>
        );
      })}
      <div>
        <Button
          type="button"
          variant="outline"
          disabled={bindings.length >= 64}
          onClick={() => setEditing({})}
        >
          <PlusIcon />
          Add environment
        </Button>
      </div>
      <ModalFrame
        open={editing !== undefined}
        onOpenChange={(open) => {
          if (!open) setEditing(undefined);
        }}
        title={editing?.group ? "Edit environment" : "Add environment"}
        description="Choose a Device, its access policy and working directories. Changes stay in this draft until you save the enclosing settings."
        closeLabel="Cancel"
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
        open={!!removing}
        onOpenChange={(open) => {
          if (!open) setRemoving(undefined);
        }}
        title="Remove directory selection?"
        description="This removes the directory from the selection. Files, the Device connection and captured Runs stay unchanged."
        closeLabel="Cancel"
        footer={
          <>
            <Button
              type="button"
              variant="outline"
              onClick={() => setRemoving(undefined)}
            >
              Cancel
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
              Remove selection
            </Button>
          </>
        }
      >
        <p className="break-all">{removing?.working_directory}</p>
        {removingDefault && (
          <ChoiceField
            label="Replacement default"
            placeholder="Choose a directory"
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
  const [error, setError] = useState<Error>();
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
    <div className="flex min-w-0 flex-col gap-4">
      <ChoiceField
        label="Device"
        placeholder="Choose a Device"
        value={deviceId}
        onValueChange={selectDevice}
        options={[
          ...(deviceId && !availableDevices.some((item) => item.id === deviceId)
            ? [{ value: deviceId, label: `${deviceId} · Not configured` }]
            : []),
          ...availableDevices.map((item) => ({
            value: item.id,
            label: item.name,
          })),
        ]}
      />
      <ConnectDevice
        label="Connect new Device"
        onConnected={(device) => {
          setConnected(device);
          selectDevice(device.id);
        }}
      />
      <ChoiceField
        label="Allowed actions"
        value={permission}
        onValueChange={setPermission}
        placeholder="Keep existing permissions"
        options={[
          { value: "read_only", label: "Read only" },
          { value: "full", label: "Full control" },
        ]}
        description={
          permission === "full"
            ? "Applies to every directory below. Allows files, commands and computer use where enabled on the Device. Desktop access is not limited to these directories; screenshots may be sent to the model and saved in history."
            : permission === "read_only"
              ? "Applies to every directory below. Read and browse files without changes, commands or desktop access."
              : "Existing permissions stay unchanged for every directory unless you choose an option."
        }
      />
      {deviceId &&
        directories.map((directory, index) => (
          <div
            key={`${deviceId}:${index}`}
            className="flex min-w-0 flex-col gap-2 rounded-lg bg-muted/40 p-3"
          >
            <DeviceDirectory
              deviceId={deviceId}
              value={directory.path}
              onChange={(path) => updateDirectory(index, { path })}
              label={
                index === 0
                  ? "Working directory"
                  : `Working directory ${index + 1}`
              }
            />
            <details>
              <summary className="cursor-pointer text-xs text-muted-foreground">
                Advanced routing
              </summary>
              <TextField
                label={
                  index === 0
                    ? "Directory alias"
                    : `Directory alias ${index + 1}`
                }
                value={directory.alias}
                onChange={(alias) => updateDirectory(index, { alias })}
                description="An internal routing name. Leave new aliases empty to generate them automatically."
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
                  Discard directory
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
            Add another directory
          </Button>
        </div>
      )}
      <ErrorNotice error={error} />
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={cancel}>
          Cancel
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
                setError(
                  new Error(
                    "Choose unique directory aliases using lowercase letters, numbers, hyphens or underscores. Host mount names are reserved.",
                  ),
                );
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
          Use environment
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
          ? "Online · Check"
          : "Offline · Retry"
        : "Check connection"}
    </Button>
  );
}
