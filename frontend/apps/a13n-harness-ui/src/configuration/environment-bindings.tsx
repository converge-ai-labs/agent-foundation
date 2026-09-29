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

export type EnvironmentBinding = Schema<"EnvironmentBindingSelection">;

export function BindingSummary({
  bindings,
  defaultEnvironment,
}: {
  bindings: readonly EnvironmentBinding[];
  defaultEnvironment?: string | null;
}) {
  const { client } = useTransport();
  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: ({ signal }) => result(client.GET("/api/devices", { signal })),
    enabled: bindings.length > 0,
  });
  return (
    <div className="flex flex-col gap-2">
      <p>Default: {defaultEnvironment ?? "Local workspace or Thread files"}</p>
      {bindings.map((binding) => (
        <div key={binding.alias} className="flex flex-col gap-1 text-sm">
          <strong>{binding.alias}</strong>
          <span>
            {devices.data?.find((item) => item.id === binding.device_id)
              ?.name ?? binding.device_id}
          </span>
          <span className="break-all">{binding.working_directory}</span>
          <span>{permissionLabel(binding.permission_ceiling)}</span>
          {binding.permission_ceiling && (
            <details>
              <summary className="cursor-pointer text-muted-foreground">
                Allowed actions
              </summary>
              <small>
                {binding.permission_ceiling.operations?.join(", ") || "None"}
              </small>
            </details>
          )}
        </div>
      ))}
    </div>
  );
}

export function EnvironmentBindings({
  bindings,
  defaultEnvironment,
  localRoots,
  allowUnsetDefault = false,
  allowEmpty = true,
  onChange,
}: {
  bindings: EnvironmentBinding[];
  defaultEnvironment?: string | null;
  localRoots: readonly string[];
  allowUnsetDefault?: boolean;
  allowEmpty?: boolean;
  onChange: (
    bindings: EnvironmentBinding[],
    defaultEnvironment: string | null,
  ) => void;
}) {
  const { client } = useTransport();
  const devices = useQuery({
    queryKey: ["devices"],
    queryFn: ({ signal }) => result(client.GET("/api/devices", { signal })),
  });
  const [editing, setEditing] = useState<number | "new" | null>(null);
  const [removing, setRemoving] = useState<string | null>(null);
  const [replacement, setReplacement] = useState("");
  const local = localRoots.map((path, index) => ({
    value: index ? `workspace-${index + 1}` : "workspace",
    label: `${index ? `workspace-${index + 1}` : "workspace"} · ${path}`,
  }));
  const choices = [
    ...local,
    { value: "thread-files", label: "Thread files" },
    ...bindings.map((item) => ({
      value: item.alias,
      label: `${item.alias} · ${item.working_directory}`,
    })),
  ];
  const selectedDefault =
    defaultEnvironment ??
    (allowUnsetDefault ? "" : localRoots.length ? "workspace" : "thread-files");
  const missingDefault =
    !!selectedDefault &&
    !choices.some((item) => item.value === selectedDefault);
  const removingDefault = removing === selectedDefault;
  return (
    <section className="flex flex-col gap-4" aria-label="Environment bindings">
      <p className="text-sm text-muted-foreground">
        Each added environment selects a Device and working directory. Changes
        are saved with the enclosing settings; they do not change active or
        historical Runs.
      </p>
      <ErrorNotice error={devices.error} />
      {!allowEmpty && bindings.length === 1 && (
        <p className="text-sm text-muted-foreground">
          Add a local Project folder or another environment before removing the
          last binding.
        </p>
      )}
      {bindings.map((binding, index) => (
        <div
          key={binding.alias}
          className="flex flex-col gap-3 rounded-lg bg-muted/40 p-3 sm:flex-row sm:items-center sm:justify-between"
        >
          <div className="min-w-0 flex-1">
            <strong>{binding.alias}</strong>
            <p className="text-sm">
              {devices.data?.find((item) => item.id === binding.device_id)
                ?.name ?? binding.device_id}
              {devices.data &&
                !devices.data.some((item) => item.id === binding.device_id) &&
                " · Not configured"}
            </p>
            <p className="break-all text-sm">{binding.working_directory}</p>
            <p className="text-xs text-muted-foreground">
              {permissionLabel(binding.permission_ceiling)}
            </p>
          </div>
          <div className="flex gap-2">
            <BindingStatus deviceId={binding.device_id} />
            <Button
              type="button"
              size="sm"
              variant="outline"
              onClick={() => setEditing(index)}
              aria-label={`Edit ${binding.alias}`}
            >
              Edit
            </Button>
            <Button
              type="button"
              size="sm"
              variant="ghost"
              onClick={() => {
                setRemoving(binding.alias);
                setReplacement("");
              }}
              aria-label={`Remove ${binding.alias}`}
              disabled={!allowEmpty && bindings.length === 1}
            >
              Remove
            </Button>
          </div>
        </div>
      ))}
      <ChoiceField
        label="Default working location"
        value={selectedDefault}
        onValueChange={(value) => onChange(bindings, value || null)}
        options={[
          ...(allowUnsetDefault && !bindings.length
            ? [
                {
                  value: "",
                  label:
                    "Keep Thread default / automatic for new conversations",
                },
              ]
            : []),
          ...(missingDefault
            ? [
                {
                  value: selectedDefault,
                  label:
                    selectedDefault === "workspace-0"
                      ? "Removed local folder · Choose a replacement"
                      : `${selectedDefault} · Not selected`,
                  disabled: true,
                },
              ]
            : []),
          ...choices,
        ]}
      />
      {missingDefault && (
        <p role="alert">
          Choose a default from the selected environments before saving.
        </p>
      )}
      <div>
        <Button
          type="button"
          variant="outline"
          onClick={() => setEditing("new")}
        >
          <PlusIcon />
          Add environment
        </Button>
      </div>
      <ModalFrame
        open={editing !== null}
        onOpenChange={(open) => {
          if (!open) setEditing(null);
        }}
        title={editing === "new" ? "Add environment" : "Edit environment"}
        description="Choose a known path or browse the Device. This does not start a Run or create a directory."
        closeLabel="Cancel"
      >
        {editing !== null && (
          <BindingEditor
            key={editing}
            initial={editing === "new" ? undefined : bindings[editing]}
            devices={devices.data ?? []}
            usedAliases={bindings
              .filter((_, index) => index !== editing)
              .map((item) => item.alias)}
            save={(binding) => {
              const previous =
                editing === "new" ? undefined : bindings[editing];
              const next =
                editing === "new"
                  ? [...bindings, binding]
                  : bindings.map((item, index) =>
                      index === editing ? binding : item,
                    );
              onChange(
                next,
                previous?.alias === selectedDefault
                  ? binding.alias
                  : selectedDefault ||
                      (localRoots.length ? "workspace" : "thread-files"),
              );
              setEditing(null);
            }}
            cancel={() => setEditing(null)}
          />
        )}
      </ModalFrame>
      <ModalFrame
        open={removing !== null}
        onOpenChange={(open) => {
          if (!open) setRemoving(null);
        }}
        title="Remove environment selection?"
        description="This forgets the selection only. It does not delete remote files, stop the Device, or rewrite captured Runs. No Device connection is required."
        closeLabel="Cancel"
        footer={
          <>
            <Button
              type="button"
              variant="outline"
              onClick={() => setRemoving(null)}
            >
              Cancel
            </Button>
            <Button
              type="button"
              disabled={removingDefault && !replacement}
              onClick={() => {
                onChange(
                  bindings.filter((item) => item.alias !== removing),
                  removingDefault ? replacement : selectedDefault || null,
                );
                setRemoving(null);
              }}
            >
              Remove selection
            </Button>
          </>
        }
      >
        <p>{removing}</p>
        {removingDefault && (
          <ChoiceField
            label="Replacement default"
            placeholder="Choose a default"
            value={replacement}
            onValueChange={setReplacement}
            options={choices.filter((item) => item.value !== removing)}
          />
        )}
      </ModalFrame>
    </section>
  );
}

function BindingEditor({
  initial,
  devices,
  usedAliases,
  save,
  cancel,
}: {
  initial?: EnvironmentBinding;
  devices: Schema<"DeviceSummary">[];
  usedAliases: string[];
  save: (binding: EnvironmentBinding) => void;
  cancel: () => void;
}) {
  const [deviceId, setDeviceId] = useState(initial?.device_id ?? "");
  const [alias, setAlias] = useState(initial?.alias ?? "");
  const [path, setPath] = useState(initial?.working_directory ?? "");
  const [permission, setPermission] = useState<string | undefined>(
    initial ? permissionPreset(initial.permission_ceiling) : "full",
  );
  const [error, setError] = useState<Error>();
  const [connected, setConnected] = useState<Schema<"DeviceSummary">>();
  const availableDevices =
    connected && !devices.some((item) => item.id === connected.id)
      ? [...devices, connected]
      : devices;
  const selectDevice = (id: string, name: string) => {
    setDeviceId(id);
    setPath("");
    if (!alias) setAlias(suggestAlias(name, usedAliases));
  };
  const missingDevice =
    deviceId && !availableDevices.some((item) => item.id === deviceId);
  return (
    <div className="flex flex-col gap-4">
      <ChoiceField
        label="Device"
        placeholder="Choose a Device"
        value={deviceId}
        onValueChange={(value) => {
          selectDevice(
            value,
            availableDevices.find((item) => item.id === value)?.name ??
              "device",
          );
        }}
        options={[
          ...(missingDevice
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
          selectDevice(device.id, device.name);
        }}
      />
      {deviceId && (
        <DeviceDirectory
          key={deviceId}
          deviceId={deviceId}
          value={path}
          onChange={setPath}
        />
      )}
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
            ? "Allows files, commands and computer use where enabled on the Device. Desktop access is not limited to this directory; screenshots may be sent to the model and saved in history."
            : permission === "read_only"
              ? "Read and browse files without changes, commands or desktop access."
              : "Existing permissions stay unchanged unless you choose an option."
        }
      />
      <TextField
        label="Environment alias"
        value={alias}
        onChange={setAlias}
        description="A unique name such as build or references. Host mount names are reserved."
      />
      <ErrorNotice error={error} />
      <div className="flex justify-end gap-2">
        <Button type="button" variant="outline" onClick={cancel}>
          Cancel
        </Button>
        <Button
          type="button"
          disabled={!deviceId || !path || !alias}
          onClick={() => {
            if (usedAliases.includes(alias) || reservedAlias(alias)) {
              setError(
                new Error(
                  "Choose a unique alias that is not reserved for Host mounts.",
                ),
              );
              return;
            }
            if (
              !/^[a-z][a-z0-9]*(?:[-_][a-z0-9]+)*$/.test(alias) ||
              alias.length > 63
            ) {
              setError(
                new Error(
                  "Use a lowercase alias with letters, numbers, hyphens or underscores.",
                ),
              );
              return;
            }
            save({
              ...initial,
              device_id: deviceId,
              alias,
              working_directory: path,
              permission_ceiling:
                permission === "read_only"
                  ? { operations: readOnly }
                  : permission === "full"
                    ? { operations: fullControl }
                    : initial?.permission_ceiling,
            });
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
