import { ChoiceField } from "a13n-ui";
import type { Schema } from "../transport/client";
import { BindingSummary, EnvironmentBindings } from "./environment-bindings";
import { ProjectFolders } from "./project-folders";

import {
  changeLocalRoots,
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
      <section className="flex flex-col gap-3" aria-label="Local environment">
        <div>
          <h3 className="font-medium">Local · Harness server</h3>
          <p className="text-sm text-muted-foreground">
            On the computer running Harness, not your browser. One mode covers
            local folders and Thread files.
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
      <section className="flex flex-col gap-3" aria-label="Device environments">
        <h3 className="font-medium">Device environments</h3>
        <EnvironmentBindings
          bindings={value.environment_bindings ?? []}
          localRoots={roots}
          defaultEnvironment={value.default_environment}
          allowUnsetDefault={allowUnsetDefault}
          allowEmpty={!requireWorkspace || roots.length > 0}
          onChange={(environment_bindings, default_environment) =>
            onChange({ environment_bindings, default_environment })
          }
        />
      </section>
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
  return (
    <div className="flex flex-col gap-2 text-sm">
      <p>
        Local · Harness server ·{" "}
        {profile?.name ?? value.environment_profile_id ?? "Default mode"}
      </p>
      {(value.local_roots ?? []).map((path, index) => (
        <p key={path} className="break-all">
          {localAlias(index)} · {path}
        </p>
      ))}
      {!value.local_roots?.length && (
        <p className="text-muted-foreground">
          Thread files only on this server
        </p>
      )}
      <BindingSummary
        bindings={value.environment_bindings ?? []}
        defaultEnvironment={value.default_environment}
      />
    </div>
  );
}
