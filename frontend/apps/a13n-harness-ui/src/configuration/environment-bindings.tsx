import { useState } from "react";
import { useQuery } from "@tanstack/react-query";
import { Link } from "react-router";
import { Button, ChoiceField, ModalFrame } from "a13n-ui";
import { PlusIcon } from "@phosphor-icons/react";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ErrorNotice, TextField } from "../shell/ui";
import { DeviceDirectory } from "./device-directory";

export type EnvironmentBinding = Schema<"EnvironmentBindingSelection">;
const readOnly: Schema<"EnvironmentAction">[] = [
  "environment.file.stat",
  "environment.file.read_text",
  "environment.file.read_bytes",
  "environment.file.list",
  "environment.file.query",
  "environment.file.search_text",
  "environment.file.copy_source",
];

export function BindingSummary({
  bindings,
  defaultEnvironment,
}: {
  bindings: readonly EnvironmentBinding[];
  defaultEnvironment?: string | null;
}) {
  return (
    <div className="flex flex-col gap-2">
      <p>Default: {defaultEnvironment ?? "Local workspace or Thread files"}</p>
      {bindings.map((binding) => (
        <div key={binding.alias} className="flex flex-col gap-1 text-sm">
          <strong>{binding.alias}</strong>
          <span>{binding.device_id}</span>
          <span className="break-all">{binding.working_directory}</span>
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
          className="flex flex-wrap items-center justify-between gap-3 rounded-lg bg-muted/40 p-3"
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
          </div>
          <div className="flex gap-2">
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
        label="Default working environment"
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
                  label: `${selectedDefault} · Not selected`,
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
  const [permission, setPermission] = useState(
    initial?.permission_ceiling ? "existing" : "full",
  );
  const [error, setError] = useState<Error>();
  const missingDevice =
    deviceId && !devices.some((item) => item.id === deviceId);
  return (
    <div className="flex flex-col gap-4">
      <ChoiceField
        label="Device"
        placeholder="Choose a Device"
        value={deviceId}
        onValueChange={(value) => {
          setDeviceId(value);
          setPath("");
        }}
        options={[
          ...(missingDevice
            ? [{ value: deviceId, label: `${deviceId} · Not configured` }]
            : []),
          ...devices.map((item) => ({ value: item.id, label: item.name })),
        ]}
      />
      {!devices.length && (
        <p>
          No Devices configured.{" "}
          <Link to="/settings/environments">
            Configure a Device in Settings
          </Link>
          .
        </p>
      )}
      <TextField
        label="Environment alias"
        value={alias}
        onChange={setAlias}
        description="A unique name such as build or references. Host mount names are reserved."
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
        options={[
          ...(initial?.permission_ceiling
            ? [{ value: "existing", label: "Keep existing action ceiling" }]
            : []),
          { value: "full", label: "Files and execution" },
          { value: "read_only", label: "Read-only files" },
        ]}
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
            if (usedAliases.includes(alias)) {
              setError(new Error("Choose a unique environment alias."));
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
              device_id: deviceId,
              alias,
              working_directory: path,
              ...(permission === "read_only"
                ? { permission_ceiling: { operations: readOnly } }
                : permission === "existing"
                  ? { permission_ceiling: initial?.permission_ceiling }
                  : {}),
            });
          }}
        >
          Use environment
        </Button>
      </div>
    </div>
  );
}
