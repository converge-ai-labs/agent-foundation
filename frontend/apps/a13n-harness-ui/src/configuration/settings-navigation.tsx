import { useContext } from "react";
import { useTranslation } from "../i18n";
import { Link, useLocation } from "react-router";
import {
  ArrowLeft,
  Users,
  ChatCircle,
  Brain,
  Robot,
  Bug,
  SlidersHorizontal,
  HeadCircuit,
  Cube,
  Monitor,
  Plug,
  FileCode,
} from "@phosphor-icons/react";
import { useSources } from "../transport/context";
import { DraftContext } from "./drafts";
import { readDocument } from "./documents";
import styles from "../shell/workbench.module.css";
import layout from "./settings.module.css";

export function SettingsNavigation({ backTo }: { backTo: string }) {
  const { t } = useTranslation();
  const groups = [
    {
      label: t("Preferences"),
      items: [
        { path: "/settings", label: t("Preferences"), icon: SlidersHorizontal },
        { path: "/settings/people", label: t("People & profile"), icon: Users },
      ],
    },
    {
      label: t("Workspace"),
      items: [
        { path: "/settings/chats", label: t("Chats"), icon: ChatCircle },
        { path: "/settings/agents", label: t("Agents"), icon: HeadCircuit },
        { path: "/settings/bot", label: t("Bot"), icon: Robot },
        { path: "/settings/models", label: t("Models"), icon: Cube },
        { path: "/settings/memory", label: t("Memory"), icon: Brain },
        {
          path: "/settings/environments",
          label: t("Environments"),
          icon: Monitor,
        },
        { path: "/settings/connections", label: t("Connections"), icon: Plug },
      ],
    },
    {
      label: t("Advanced"),
      items: [
        {
          path: "/settings/resources",
          label: t("Configuration files"),
          icon: FileCode,
        },
        { path: "/settings/diagnostics", label: t("Diagnostics"), icon: Bug },
      ],
    },
  ];
  const location = useLocation();
  const sources = useSources();
  const drafts = useContext(DraftContext);
  const path = new URLSearchParams(location.search).get("path") ?? "";
  const source = sources.data?.sources.find(
    (item) => item.relative_path === path,
  );
  const sourceKind =
    source?.resource_kind ??
    readDocument(drafts.get(path)?.content ?? "")?.get("kind");
  const sourceSection =
    sourceKind === "root"
      ? "/settings/resources"
      : sourceKind === "project"
        ? "/projects"
        : sourceKind === "model"
          ? "/settings/models"
          : sourceKind === "agent"
            ? "/settings/agents"
            : sourceKind === "harness_plugin" || sourceKind === "web_provider"
              ? "/settings/connections"
              : sourceKind === "environment_profile" ||
                  sourceKind === "device" ||
                  sourceKind === "environment_run_extension"
                ? "/settings/environments"
                : sourceKind === "mcp_server"
                  ? "/settings/connections"
                  : "/settings/resources";
  return (
    <div className={styles.settingsNavigation}>
      <Link className={styles.backToChats} to={backTo}>
        <ArrowLeft size={16} />
        {t("Back to chats")}
      </Link>
      <nav
        className={`${styles.settingsNav} a13n-scrollbar`}
        aria-label={t("Settings sections")}
      >
        {groups.map((group) => (
          <div key={group.label} className={layout.navGroup}>
            <p>{group.label}</p>
            {group.items.map(({ path, label, icon: Icon }) => {
              const active =
                location.pathname === "/settings/source"
                  ? sourceSection === path
                  : location.pathname === path ||
                    (path !== "/settings" &&
                      location.pathname.startsWith(`${path}/`));
              return (
                <Link
                  key={path}
                  to={path}
                  aria-current={active ? "page" : undefined}
                  className={active ? styles.activeNav : styles.navLink}
                >
                  <Icon
                    aria-hidden="true"
                    size={18}
                    weight={active ? "duotone" : "regular"}
                  />
                  {label}
                </Link>
              );
            })}
          </div>
        ))}
      </nav>
    </div>
  );
}
