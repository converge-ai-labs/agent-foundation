import {
  SettingsChoice as ChoiceField,
  SettingsSection,
} from "../shell/settings";
import styles from "./environments.module.css";

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
    <div className={styles.editor}>
      <SettingsSection
        title="Local"
        description="The computer running Harness."
      >
        <ChoiceField
          label="Local mode"
          description={
            profile?.mode === "full-control"
              ? "Runs as the server account without a sandbox."
              : profile?.description
          }
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
      </SettingsSection>
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
    <div className={styles.summary}>
      <section className={styles.summaryEnvironment}>
        <p className="font-medium">Local · Harness server</p>
        <p className="text-muted-foreground">
          {profile?.name ?? value.environment_profile_id ?? "Default mode"}
          {value.local_roots?.length
            ? ` · ${directoryCount(value.local_roots.length)}`
            : " · Chat files only"}
        </p>
        {(value.local_roots ?? []).map((path, index) => (
          <p key={path} className={styles.summaryPath}>
            {path}
            {localAlias(index) === selected && (
              <span className="ml-2 text-muted-foreground">Default</span>
            )}
          </p>
        ))}
        {selected === "thread-files" && <p>Chat files · Default</p>}
      </section>
      <BindingSummary
        bindings={value.environment_bindings ?? []}
        defaultEnvironment={selected}
      />
    </div>
  );
}
