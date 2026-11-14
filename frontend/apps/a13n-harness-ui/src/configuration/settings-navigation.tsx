import { useContext } from "react";
import { useTranslation } from "../i18n";
import { Link, useLocation } from "react-router";
import {
  ArrowLeft,
  PaintBrush,
  SlidersHorizontal,
  HeadCircuit,
  Cube,
  Monitor,
  Key,
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
  const sections = [
    { path: "/settings", label: t("General"), icon: SlidersHorizontal },
    { path: "/settings/appearance", label: t("Appearance"), icon: PaintBrush },
    { path: "/settings/agents", label: t("Agents"), icon: HeadCircuit },
    { path: "/settings/models", label: t("Models"), icon: Cube },
    { path: "/settings/environments", label: t("Environments"), icon: Monitor },
    { path: "/settings/accounts", label: t("Accounts & API keys"), icon: Key },
    { path: "/settings/connections", label: t("Connections"), icon: Plug },
    {
      path: "/settings/resources",
      label: t("Configuration files"),
      icon: FileCode,
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
      ? "/settings"
      : sourceKind === "project"
        ? "/projects"
        : sourceKind === "model"
          ? "/settings/models"
          : sourceKind === "agent"
            ? "/settings/agents"
            : sourceKind === "harness_plugin"
              ? "/settings/connections"
              : sourceKind === "environment_profile" ||
                  sourceKind === "device" ||
                  sourceKind === "environment_run_extension"
                ? "/settings/environments"
                : sourceKind === "mcp_server"
                  ? "/settings/connections"
                  : "/settings/resources";
  return (
    <div className={`${styles.settingsNavigation} ${layout.navigation}`}>
      <Link className={styles.backToChats} to={backTo}>
        <ArrowLeft size={16} />
        {t("Back to chats")}
      </Link>
      <h2 className={styles.settingsNavigationTitle}>{t("Settings")}</h2>
      <nav
        className={`${styles.settingsNav} a13n-scrollbar`}
        aria-label={t("Settings sections")}
      >
        {sections.map(({ path, label, icon: Icon }) => {
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
              className={`${active ? styles.activeNav : styles.navLink} ${path === "/settings/resources" ? layout.utilityLink : ""}`}
            >
              <Icon aria-hidden="true" size={16} weight="regular" />
              {label}
            </Link>
          );
        })}
      </nav>
    </div>
  );
}
