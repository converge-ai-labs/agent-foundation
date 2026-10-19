import { Checkbox } from "a13n-ui";
import type { Dispatch, SetStateAction } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { ToolPermissions } from "./tool-permissions";
import styles from "./agents.module.css";

type Selection = Schema["PlatformSelection"];

const groups: {
  key: Schema["PlatformGroup"];
  name: string;
  description: string;
}[] = [
  {
    key: "agents",
    name: "Agents",
    description: "Agents, their versions and the tool catalog.",
  },
  {
    key: "models",
    name: "Models",
    description: "Models and the model catalog.",
  },
  {
    key: "connections",
    name: "Connections",
    description: "Connections, their tools and connector apps.",
  },
  { key: "skills", name: "Skills", description: "Skills and their versions." },
  {
    key: "environment_templates",
    name: "Environment templates",
    description: "Environment templates.",
  },
  {
    key: "providers",
    name: "Providers",
    description: "Providers, and tests of their saved connections.",
  },
  {
    key: "execution",
    name: "Execution",
    description:
      "Sessions, threads, runs, traces and findings; its only write submits a finding.",
  },
];

const supported = ["allow", "ask", "deny"] as const;

/** The groups of Service operations the agent calls as tools, and one permission for its reads and one for its writes. */
export function PlatformTools({
  value,
  onChange,
  readOnly = false,
}: {
  value: Selection;
  onChange: Dispatch<SetStateAction<Selection>>;
  readOnly?: boolean;
}) {
  const { t } = useTranslation();
  const selected = new Set(value.groups ?? []);
  function toggle(group: Schema["PlatformGroup"], checked: boolean) {
    onChange((previous) => {
      const current = new Set(previous.groups ?? []);
      if (checked) current.add(group);
      else current.delete(group);
      return {
        ...previous,
        groups: groups.map(({ key }) => key).filter((key) => current.has(key)),
      };
    });
  }
  const permissions = [
    {
      field: "read_permission",
      name: t("Reads"),
      label: t("Platform read permission"),
      value: value.read_permission ?? "allow",
    },
    {
      field: "write_permission",
      name: t("Writes"),
      label: t("Platform write permission"),
      value: value.write_permission ?? "ask",
    },
  ] as const;
  // Group and permission rows share one grid, so names and descriptions align.
  return (
    <div className={styles.toolsetCard}>
      {groups.map((group) => (
        <div className={styles.platformRow} key={group.key}>
          <label className={styles.platformGroup}>
            <Checkbox
              disabled={readOnly}
              checked={selected.has(group.key)}
              onCheckedChange={(checked) => toggle(group.key, checked === true)}
            />
            <span className={styles.toolsetGroupName}>{t(group.name)}</span>
          </label>
          <span className={styles.toolsetGroupDescription}>
            {t(group.description)}
          </span>
        </div>
      ))}
      {permissions.map((permission) => (
        <div className={styles.platformRow} key={permission.field}>
          <span
            className={`${styles.toolsetGroupName} ${styles.platformPermission}`}
          >
            {permission.name}
          </span>
          <span className={styles.toolsetGroupDescription}>
            {t("Applies to every selected group.")}
          </span>
          <ToolPermissions
            name={permission.name}
            label={permission.label}
            value={permission.value}
            supported={supported}
            readOnly={readOnly}
            onChange={(choice) => {
              if (choice !== "review")
                onChange((previous) => ({
                  ...previous,
                  [permission.field]: choice,
                }));
            }}
          />
        </div>
      ))}
    </div>
  );
}
