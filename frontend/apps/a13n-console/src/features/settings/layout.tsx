import { Button, FormField, Input } from "a13n-ui";

import { ArrowLeftIcon, CaretDownIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useState } from "react";
import { Link, useSearchParams } from "react-router";
import { PageActionsTarget } from "../../shared/page-actions";

import { useTranslation } from "react-i18next";
import { useSettingsNavigation, type SettingsScope } from "./navigation";
import { providerCategory } from "../providers/categories";
import styles from "./settings.module.css";

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
  environments: "Shared templates for agent execution.",
  workspaces: "Separate resources, members, and work into workspaces.",
};
export function SettingsLayout({
  scope,
  content,
}: {
  scope: SettingsScope;
  content: Record<string, ReactNode>;
}) {
  const { t } = useTranslation();
  const [search] = useSearchParams();
  const [actionsTarget, setActionsTarget] = useState<HTMLDivElement | null>(
    null,
  );
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const groups = useSettingsNavigation();
  const current = groups.find((group) => group.scope === scope)!;
  const selected =
    current.sections.find(
      (item) => item.value === search.get("section") && item.value in content,
    ) ?? current.sections.find((item) => item.value in content)!;
  const isForm = ["profile", "preferences", "security"].includes(
    selected.value,
  );
  const visible = groups
    .map((group) => ({
      ...group,
      sections: group.sections.filter((item) =>
        `${t(group.label)} ${group.name ?? ""} ${t(item.label)}`
          .toLocaleLowerCase()
          .includes(filter.toLocaleLowerCase()),
      ),
    }))
    .filter((group) => group.sections.length);
  return (
    <PageActionsTarget value={actionsTarget}>
      <div className={styles.layout}>
        <div className={styles.mobileNavigation}>
          <Button
            variant="ghost"
            aria-expanded={navigationOpen}
            aria-controls="settings-outline"
            onClick={() => setNavigationOpen(!navigationOpen)}
            type="button"
          >
            {t("Settings")}
            <CaretDownIcon size={14} />
          </Button>
          <span>{t(selected.label)}</span>
        </div>
        <aside
          id="settings-outline"
          className={`${styles.outline} a13n-scrollbar`}
          data-open={navigationOpen}
        >
          <Link className={styles.back} to="/">
            <ArrowLeftIcon size={14} />
            {t("Back to workspace")}
          </Link>
          <div className={styles.settingsSearch}>
            <FormField
              className="min-w-0 w-full"
              label={t("Search settings")}
              hideLabel={true}
            >
              <Input
                placeholder={t("Search settings…")}
                value={filter}
                onChange={(event) => setFilter(event.target.value)}
                type="search"
              />
            </FormField>
          </div>
          <nav aria-label={t("Settings navigation")}>
            {visible.map((group) => (
              <section
                className={styles.scopeGroup}
                key={group.scope}
                data-scope={group.scope}
              >
                <h2 className={styles.scopeLabel}>{t(group.label)}</h2>
                {group.name && (
                  <p className={styles.scopeName} title={group.name}>
                    {group.name}
                  </p>
                )}
                <div className={styles.sectionLinks}>
                  {group.sections.map((item) => (
                    <Link
                      key={item.value}
                      onClick={() => setNavigationOpen(false)}
                      to={item.href ?? `${group.path}?section=${item.value}`}
                      aria-current={
                        group.scope === scope && item.value === selected.value
                          ? "page"
                          : undefined
                      }
                    >
                      <item.icon size={14} />
                      {t(item.label)}
                    </Link>
                  ))}
                </div>
              </section>
            ))}
            {!visible.length && (
              <p className={styles.noResults}>{t("No matching settings")}</p>
            )}
          </nav>
        </aside>
        <div className={styles.surface} data-settings-part="surface">
          <div
            className={styles.content}
            data-settings-part="content"
            data-content={isForm ? "form" : "collection"}
            key={`${scope}:${selected.value}`}
          >
            <header className={styles.heading} data-settings-part="heading">
              <div>
                <h1>{t(selected.label)}</h1>
                {selected.value !== "profile" && (
                  <p>
                    {t(
                      selected.value === "providers"
                        ? providerCategory(search.get("category")).description
                        : (descriptions[selected.value] ??
                            "Manage settings for this space."),
                    )}
                  </p>
                )}
              </div>
              <div className={styles.headingActions} ref={setActionsTarget} />
            </header>
            {content[selected.value]}
          </div>
        </div>
      </div>
    </PageActionsTarget>
  );
}
