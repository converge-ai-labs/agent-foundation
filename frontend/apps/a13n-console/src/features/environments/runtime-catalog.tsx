import {
  CheckIcon,
  CloudIcon,
  DesktopIcon,
  CubeIcon,
} from "@phosphor-icons/react";
import { Input, Tabs, TabsList, TabsTab, TabsPanel } from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { ProviderDefinition } from "../providers/add-provider-dialog";
import { ProviderIcon } from "../../shared/identity";
import styles from "./environments.module.css";

const ownInfrastructure = new Set(["local", "direct_local", "docker"]);
const cloudServices = new Set([
  "e2b",
  "daytona",
  "modal",
  "runloop",
  "sprites",
  "vercel",
]);

/** Presentation only; unknown extension types must not imply a hosting guarantee. */
export function runtimeGroup(type: string): "cloud" | "own" | "other" {
  return ownInfrastructure.has(type)
    ? "own"
    : cloudServices.has(type)
      ? "cloud"
      : "other";
}

export function EnvironmentRuntimeCatalog({
  definitions,
  selected,
  onChoose,
  searchable = false,
}: {
  definitions: readonly ProviderDefinition[];
  selected?: string;
  onChoose: (type: string) => void;
  searchable?: boolean;
}) {
  const { t } = useTranslation();
  const [search, setSearch] = useState("");
  const [groupChoice, setGroupChoice] = useState("cloud");
  const remembered = useRef<Record<string, string>>({});
  const query = search.trim().toLocaleLowerCase();
  const visible = definitions.filter((item) =>
    `${item.type} ${item.display_name} ${["local", "direct_local"].includes(item.type) ? t("Local directory") : ""}`
      .toLocaleLowerCase()
      .includes(query),
  );
  const groups = [
    {
      key: "cloud",
      title: "Cloud hosting",
      icon: CloudIcon,
      description: "Use a sandbox service with your team's account.",
    },
    {
      key: "own",
      title: "Own hosting",
      icon: DesktopIcon,
      description:
        "Use infrastructure managed by your deployment administrator.",
    },
    {
      key: "other",
      title: "Other runtimes",
      icon: CubeIcon,
      description:
        "Additional runtime integrations offered by this deployment.",
    },
  ] as const;
  const available = groups.filter((group) =>
    definitions.some((item) => runtimeGroup(item.type) === group.key),
  );
  const current = selected ? runtimeGroup(selected) : groupChoice;
  const active =
    available.find((group) => group.key === current)?.key ??
    available[0]?.key ??
    "cloud";
  function changeGroup(value: unknown) {
    const group = available.find((item) => item.key === value);
    if (!group || group.key === active) return;
    if (selected) remembered.current[active] = selected;
    setGroupChoice(group.key);
    setSearch("");
    const previous = remembered.current[group.key];
    // Browsing another hosting mode must not leave a hidden runtime submit-ready.
    onChoose(
      definitions.some((item) => item.type === previous) ? previous! : "",
    );
  }
  return (
    <Tabs
      value={active}
      onValueChange={changeGroup}
      className={styles.runtimeGroups}
    >
      {!!available.length && (
        <TabsList aria-label={t("Hosting mode")} className={styles.hostingTabs}>
          {available.map((group) => (
            <TabsTab key={group.key} value={group.key}>
              <group.icon aria-hidden="true" />
              {t(group.title)}
            </TabsTab>
          ))}
        </TabsList>
      )}
      {searchable && definitions.length > 6 && (
        <Input
          type="search"
          aria-label={t("Search providers…")}
          placeholder={t("Search providers…")}
          value={search}
          onChange={(event) => setSearch(event.target.value)}
        />
      )}
      {available.map((group) => {
        const choices = visible.filter(
          (item) => runtimeGroup(item.type) === group.key,
        );

        return (
          <TabsPanel
            key={group.key}
            value={group.key}
            className={styles.runtimeGroup}
          >
            <div className={styles.runtimeHeading}>
              <p>{t(group.description)}</p>
            </div>
            <div className={styles.runtimeGrid}>
              {choices.map((item) => (
                <button
                  key={item.type}
                  type="button"
                  aria-pressed={selected === item.type}
                  className={styles.runtimeChoice}
                  onClick={() => {
                    remembered.current[group.key] = item.type;
                    onChoose(item.type);
                  }}
                >
                  <ProviderIcon type={item.type} />
                  <span>
                    {["local", "direct_local"].includes(item.type)
                      ? t("Local directory")
                      : item.display_name}
                    {["local", "direct_local"].includes(item.type) && (
                      <small>{t("Development only")}</small>
                    )}
                  </span>
                  {selected === item.type && (
                    <CheckIcon
                      aria-hidden="true"
                      className={styles.runtimeCheck}
                    />
                  )}
                </button>
              ))}
            </div>
            {!choices.length && <p>{t("No results")}</p>}
          </TabsPanel>
        );
      })}
      {!definitions.length && (
        <p>{t("No environment runtimes are available in this deployment.")}</p>
      )}
    </Tabs>
  );
}
