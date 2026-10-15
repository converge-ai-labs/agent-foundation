import { ChoiceField } from "a13n-ui";
import type { Schema } from "../transport/client";
import {
  BindingSummary,
  DefaultWorkingDirectory,
  EnvironmentBindings,
} from "./environment-bindings";
import { ProjectFolders } from "./project-folders";

import {
  changeLocalRoots,
  directoryCount,
  selectedWorkingDirectory,
  localAlias,
  type EnvironmentSelection,
} from "./environment-selection";
export {
  invalidEnvironments,
  type EnvironmentSelection,
} from "./environment-selection";

/** Controlled draft only: the enclosing Project, Thread or Composer owns saving. */
export function EnvironmentsEditor({
  value,
  profiles = [],
  inheritedProfile,
  inheritLabel = "Use default",
  allowUnsetDefault = false,
  requireWorkspace = false,
  onChange,
}: {
  value: EnvironmentSelection;
  profiles?: Schema<"ThreadSelectorCatalog">["environments"];
  inheritedProfile?: string;
  inheritLabel?: string;
  allowUnsetDefault?: boolean;
  requireWorkspace?: boolean;
  onChange: (patch: EnvironmentSelection) => void;
}) {
  const roots = value.local_roots ?? [];
  const profile = profiles.find(
    (item) =>
      item.profile_id === (value.environment_profile_id ?? inheritedProfile),
  );
  return (
    <div className="flex min-w-0 flex-col gap-6">
      <section
        className="flex min-w-0 flex-col gap-3 rounded-lg bg-muted/40 p-4"
        aria-label="Local environment"
      >
        <div>
          <h3 className="font-medium">Local · Harness server</h3>
          <p className="text-sm text-muted-foreground">
            {roots.length ? directoryCount(roots.length) : "Thread files only"}{" "}
            on the computer running Harness. All local directories share this
            mode.
          </p>
        </div>
        <ChoiceField
          label="Local mode"
          value={value.environment_profile_id ?? ""}
          onValueChange={(environment_profile_id) =>
            onChange({
              environment_profile_id: environment_profile_id || undefined,
            })
          }
          options={[
            { value: "", label: inheritLabel },
            ...profiles.map((item) => ({
              value: item.profile_id,
              label: item.name,
            })),
            ...(value.environment_profile_id &&
            !profiles.some(
              (item) => item.profile_id === value.environment_profile_id,
            )
              ? [
                  {
                    value: value.environment_profile_id,
                    label: `${value.environment_profile_id} · Unavailable`,
                    disabled: true,
                  },
                ]
              : []),
          ]}
        />
        {profile && (
          <p className="text-sm text-muted-foreground">
            {profile.mode === "full-control"
              ? "Full Control runs as the server account, without a sandbox."
              : profile.description}
          </p>
        )}
        <ProjectFolders
          compact
          roots={roots.map((path) => ({ path }))}
          allowEmpty={!requireWorkspace || !!value.environment_bindings?.length}
          onChange={(next) =>
            onChange(
              changeLocalRoots(
                value,
                next.map((item) => item.path),
              ),
            )
          }
        />
      </section>
      <EnvironmentBindings
        bindings={value.environment_bindings ?? []}
        localRoots={roots}
        defaultEnvironment={value.default_environment}
        allowEmpty={!requireWorkspace || roots.length > 0}
        onChange={(environment_bindings, default_environment) =>
          onChange({ environment_bindings, default_environment })
        }
      />
      <DefaultWorkingDirectory
        value={value}
        allowUnsetDefault={allowUnsetDefault}
        onChange={(default_environment) => onChange({ default_environment })}
      />
    </div>
  );
}

export function EnvironmentsSummary({
  value,
  profiles = [],
}: {
  value: EnvironmentSelection;
  profiles?: Schema<"ThreadSelectorCatalog">["environments"];
}) {
  const profile = profiles.find(
    (item) => item.profile_id === value.environment_profile_id,
  );
  const selected = selectedWorkingDirectory(value);
  return (
    <div className="flex flex-col gap-3 text-sm">
      <section className="flex flex-col gap-2 rounded-lg bg-muted/40 p-3">
        <p className="font-medium">Local · Harness server</p>
        <p className="text-muted-foreground">
          {profile?.name ?? value.environment_profile_id ?? "Default mode"}
          {value.local_roots?.length
            ? ` · ${directoryCount(value.local_roots.length)}`
            : " · Thread files only"}
        </p>
        {(value.local_roots ?? []).map((path, index) => (
          <p key={path} className="break-all">
            {path}
            {localAlias(index) === selected && (
              <span className="ml-2 text-muted-foreground">Default</span>
            )}
          </p>
        ))}
        {selected === "thread-files" && <p>Thread files · Default</p>}
      </section>
      <BindingSummary
        bindings={value.environment_bindings ?? []}
        defaultEnvironment={selected}
      />
    </div>
  );
}
