import { Button, FormField, Input } from "a13n-ui";

import { ArrowLeftIcon, CaretDownIcon } from "@phosphor-icons/react";
import type { ReactNode } from "react";
import { useState } from "react";
import { Link, Navigate, useLocation, useParams } from "react-router";
import { PageActionsTarget } from "../../shared/page";

import { useTranslation } from "react-i18next";
import {
  resolveSection,
  useSettingsNavigation,
  type SettingsScope,
} from "./navigation";
import { providerCategory } from "../providers/categories";
import styles from "./settings.module.css";

/**
 * The contextual settings shell. Sections are addressable pages under the
 * scope's settings path; the navigation lists every scope the reader can
 * reach, the current one first, so moving between them takes one click.
 */
export function SettingsLayout({
  scope,
  section,
  heading = true,
  content,
}: {
  scope: SettingsScope;
  /** Overrides the URL segment for routes that nest below a section. */
  section?: string;
  /** Hidden when the content owns its own header, such as a detail pane. */
  heading?: boolean;
  content: Record<string, ReactNode>;
}) {
  const { t } = useTranslation();
  const location = useLocation();
  const params = useParams();
  const [actionsTarget, setActionsTarget] = useState<HTMLDivElement | null>(
    null,
  );
  const [navigationOpen, setNavigationOpen] = useState(false);
  const [filter, setFilter] = useState("");
  const groups = useSettingsNavigation();
  const current = groups.find((group) => group.scope === scope)!;
  const search = new URLSearchParams(location.search);
  const requested = section ?? params.section;
  const selected =
    current.sections.find(
      (item) =>
        item.value === resolveSection(scope, requested) &&
        item.value in content,
    ) ??
    current.sections.find((item) => item.value in content) ??
    current.sections[0];
  // Sections used to be `?section=` values; those addresses still resolve.
  const legacy = requested
    ? undefined
    : resolveSection(scope, search.get("section"));
  if (legacy) {
    search.delete("section");
    const query = search.toString();
    return (
      <Navigate
        replace
        to={`${current.path}/${legacy}${query ? `?${query}` : ""}`}
      />
    );
  }
  const term = filter.trim().toLocaleLowerCase();
  const visible = groups
    .map((group) => ({
      ...group,
      sections: group.sections.filter((item) =>
        `${t(group.label)} ${group.name ?? ""} ${t(item.label)}`
          .toLocaleLowerCase()
          .includes(term),
      ),
    }))
    .filter((group) => group.sections.length);
  const ordered = [
    ...visible.filter((group) => group.scope === scope),
    ...visible.filter((group) => group.scope !== scope),
  ];
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
          <FormField
            className={`min-w-0 w-full ${styles.search}`}
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
          <nav aria-label={t("Settings navigation")}>
            {ordered.map((group) => (
              <section
                className={styles.scope}
                key={group.scope}
                data-scope={group.scope}
              >
                <h2 className={styles.scopeLabel}>
                  {t(group.label)}
                  {group.name && (
                    <span className={styles.scopeName} title={group.name}>
                      {group.name}
                    </span>
                  )}
                </h2>
                {group.sections.map((item) => (
                  <Link
                    key={item.value}
                    onClick={() => setNavigationOpen(false)}
                    to={`${group.path}/${item.value}`}
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
              </section>
            ))}
            {!ordered.length && (
              <p className={styles.noResults}>{t("No matching settings")}</p>
            )}
          </nav>
        </aside>
        <div className={styles.surface} data-settings-part="surface">
          <div
            className={styles.content}
            data-settings-part="content"
            data-content={selected.layout ?? "collection"}
            key={`${scope}:${selected.value}`}
          >
            {heading && (
              <header className={styles.heading} data-settings-part="heading">
                <div className="min-w-0">
                  <h1>{t(selected.title ?? selected.label)}</h1>
                  {(selected.value === "providers" || selected.description) && (
                    <p>
                      {t(
                        selected.value === "providers"
                          ? providerCategory(search.get("category")).description
                          : selected.description!,
                      )}
                    </p>
                  )}
                </div>
                <div className={styles.headingActions} ref={setActionsTarget} />
              </header>
            )}
            {content[selected.value] ?? null}
          </div>
        </div>
      </div>
    </PageActionsTarget>
  );
}
