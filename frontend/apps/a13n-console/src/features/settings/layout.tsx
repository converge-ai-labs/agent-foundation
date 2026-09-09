import type { ReactNode } from "react";
import { Link, useSearchParams } from "react-router";
import {
  ArrowLeft,
  User,
  Building2,
  Layers,
  Shield,
  Users,
  KeyRound,
  Mail,
  Activity,
  Monitor,
  Settings,
  Boxes,
  Cable,
} from "lucide-react";
import { useTranslation } from "react-i18next";
import styles from "./settings.module.css";

const icons = {
  profile: User,
  general: Settings,
  security: Shield,
  members: Users,
  invitations: Mail,
  "personal-keys": KeyRound,
  "member-keys": KeyRound,
  accounts: Shield,
  audit: Activity,
  activity: Activity,
  sessions: Monitor,
  models: Boxes,
  environments: Monitor,
  connectors: Cable,
  workspaces: Layers,
};
const descriptions: Record<string, string> = {
  preferences: "Choose how Console looks and feels.",
  profile: "Manage your name and image.",
  security: "Manage how you sign in and keep your account secure.",
  members: "Manage the people who can access this space.",
  invitations: "Invite people and manage pending invitations.",
  "personal-keys": "Your credentials for this workspace. Keep them private.",
  "member-keys": "Review and revoke workspace members’ API keys.",
  accounts: "Dedicated identities for applications and automation.",
  audit: "Review changes to access and account security.",
  activity: "Review recent security activity on your account.",
  sessions: "Manage browsers signed in to your account.",
  models: "Models available across your organization.",
  environments: "Shared providers and templates for agent execution.",
  connectors: "Providers available to connections across your organization.",
  workspaces: "Separate resources, members, and work into workspaces.",
};
export function SettingsLayout({
  scope,
  name,
  items,
}: {
  scope: "personal" | "workspace" | "organization";
  name: string;
  items: { value: string; label: string; content: ReactNode }[];
}) {
  const { t } = useTranslation();
  const [search] = useSearchParams();
  const selected =
    items.find((item) => item.value === search.get("section")) ?? items[0];
  const Icon =
    scope === "personal" ? User : scope === "organization" ? Building2 : Layers;
  return (
    <div className={styles.layout}>
      <aside className={styles.outline}>
        <Link className={styles.back} to="/">
          <ArrowLeft size={14} />
          {t("Back to workspace")}
        </Link>
        <div className={styles.identity}>
          <span>
            <Icon size={18} />
          </span>
          <div>
            <strong>{name}</strong>
            <small>
              {t(
                scope === "personal"
                  ? "Personal settings"
                  : scope === "workspace"
                    ? "Workspace settings"
                    : "Organization settings",
              )}
            </small>
          </div>
        </div>
        <nav aria-label={t("Settings navigation")}>
          {items.map((item) => {
            const ItemIcon =
              item.value === "profile" && scope !== "personal"
                ? Settings
                : (icons[item.value as keyof typeof icons] ?? Settings);
            return (
              <Link
                key={item.value}
                to={{
                  search: (() => {
                    const next = new URLSearchParams(search);
                    next.set("section", item.value);
                    return next.toString();
                  })(),
                }}
                aria-current={item === selected ? "page" : undefined}
              >
                <ItemIcon size={15} />
                {item.label}
              </Link>
            );
          })}
        </nav>
      </aside>
      <div className={styles.content} key={selected.value}>
        <header className={styles.heading}>
          <span>
            {t(
              scope === "personal"
                ? "Account"
                : scope === "workspace"
                  ? "Workspace"
                  : "Organization",
            )}
          </span>
          <h1>{selected.label}</h1>
          <p>
            {t(
              descriptions[selected.value] ?? "Manage settings for this space.",
            )}
          </p>
        </header>
        {selected.content}
      </div>
    </div>
  );
}
