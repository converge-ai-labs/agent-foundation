import { Fragment } from "react";
import { Link } from "react-router";
import { Button, Checkbox } from "a13n-ui";
import { ArrowUp, ArrowDown } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { useWebProviders } from "../transport/context";
import type { Schema } from "../transport/client";
import { ErrorNotice } from "../shell/ui";
import {
  SettingsChoice,
  SettingsRow,
  SettingsSection,
  SettingsToggle,
} from "../shell/settings";
import { readDocument } from "./documents";
import { WebProviderIdentity } from "./web-providers";
import styles from "../shell/workbench.module.css";
import layout from "./settings.module.css";
import webStyles from "./web-providers.module.css";

type Operation = "search" | "scrape";
type OperationConfiguration = {
  mode?: string;
  backend?: string;
  backend_priority?: string[];
  allow_domains?: string[];
  deny_domains?: string[];
};
const LOCAL = "default";

/** Explicitly chosen backends in trial order; an exact local default is no choice. */
export function chosenBackends(current: OperationConfiguration): string[] {
  if (current.backend)
    return current.backend === LOCAL ? [] : [current.backend];
  return current.backend_priority ?? [];
}

/**
 * One choice is an exact backend and several are an ordered fallback list; an
 * existing fallback list stays one. No choice restores the implicit local default.
 */
export function backendPreference(chosen: string[], fallback = false) {
  if (!chosen.length || (chosen.length === 1 && chosen[0] === LOCAL))
    return { backend: undefined, backend_priority: undefined };
  if (chosen.length === 1 && !fallback)
    return { backend: chosen[0], backend_priority: undefined };
  return { backend: undefined, backend_priority: chosen };
}

export function WebOptions({
  source,
  index,
  onChange,
}: {
  source: string;
  index: number;
  onChange: (source: string) => void;
}) {
  const { t } = useTranslation();
  const catalog = useWebProviders();
  const document = readDocument(source)!;
  const config = document.toJS().capabilities[index].configuration ?? {};
  const patch = (operation: string, values: Record<string, unknown>) => {
    for (const [key, value] of Object.entries(values)) {
      const path = ["capabilities", index, "configuration", operation, key];
      if (value === undefined) document.deleteIn(path);
      else document.setIn(path, value);
    }
    onChange(document.toString());
  };
  const search: OperationConfiguration = config.search ?? {};
  const scrape: OperationConfiguration = config.scrape ?? {};
  const searchMode = search.mode ?? "auto";
  const scrapeMode = scrape.mode ?? "host";
  const searchRestricted = !!(
    search.allow_domains?.length || search.deny_domains?.length
  );
  const restrictedScrape = !!(
    scrape.allow_domains?.length || scrape.deny_domains?.length
  );
  const host: Record<Operation, boolean> = {
    search: searchMode === "host" || searchMode === "auto",
    scrape: scrapeMode === "host",
  };
  const operations: Record<Operation, OperationConfiguration> = {
    search,
    scrape,
  };
  const types = catalog.data?.types ?? [];
  const typeOf = (account: Schema<"WebProviderResource">) =>
    types.find((type) => type.type === account.type);
  const accounts = catalog.data?.accounts ?? [];
  const chosen = {
    search: chosenBackends(search),
    scrape: chosenBackends(scrape),
  };
  const choose = (operation: Operation, next: string[]) =>
    patch(
      operation,
      backendPreference(
        next,
        !!operations[operation].backend_priority?.length &&
          !(operation === "scrape" && restrictedScrape),
      ),
    );
  const nameOf = (id: string) =>
    id === LOCAL
      ? t("Local default")
      : (accounts.find((account) => account.id === id)?.name ??
        t("{{value}} (unavailable)", { value: id }));
  // Referenced accounts that no longer exist stay visible so they can be cleared.
  const missing = [...new Set([...chosen.search, ...chosen.scrape])].filter(
    (id) => id !== LOCAL && !accounts.some((account) => account.id === id),
  );
  const route = (operation: Operation) => {
    const list = chosen[operation];
    if (!list.length)
      return t(
        operation === "search"
          ? "Local DuckDuckGo search"
          : "Local HTML scraping",
      );
    return fallbackList(operation).map(nameOf).join(" → ");
  };
  // Harness tries bound backends missing from a priority list last, so the
  // local default always ends a fallback list unless it is moved.
  const fallbackList = (operation: Operation) => {
    const list = chosen[operation];
    return operations[operation].backend_priority?.length &&
      !list.includes(LOCAL)
      ? [...list, LOCAL]
      : list;
  };
  const ordered = (["search", "scrape"] as const).filter(
    (operation) =>
      host[operation] &&
      chosen[operation].some((id) => id !== LOCAL) &&
      !(operation === "scrape" && restrictedScrape),
  );
  return (
    <details className={styles.details}>
      <summary>
        <span>{t("Web options")}</span>
      </summary>
      <div className={styles.stack}>
        <SettingsSection>
          <SettingsChoice
            label={t("Web search")}
            value={searchMode}
            options={[
              { value: "auto", label: t("Automatic") },
              { value: "host", label: t("Host search") },
              {
                value: "native",
                label: t("Model-native search"),
                disabled: searchRestricted,
              },
              { value: "off", label: t("Off") },
            ]}
            onValueChange={(mode) =>
              patch("search", {
                mode,
                ...(mode === "off" || mode === "native"
                  ? { backend: undefined, backend_priority: undefined }
                  : {}),
              })
            }
          />
          <SettingsToggle
            label={t("Web scraping")}
            checked={scrapeMode !== "off"}
            onCheckedChange={(enabled) =>
              patch(
                "scrape",
                enabled
                  ? { mode: "host" }
                  : {
                      mode: "off",
                      backend: undefined,
                      backend_priority: undefined,
                    },
              )
            }
          />
          <SettingsToggle
            label={t("Fetch web resources")}
            checked={config.fetch?.enabled ?? true}
            onCheckedChange={(enabled) => patch("fetch", { enabled })}
          />
          <SettingsToggle
            label={t("Download files")}
            checked={config.download?.enabled ?? true}
            onCheckedChange={(enabled) => patch("download", { enabled })}
          />
        </SettingsSection>
        {(host.search || host.scrape) && (
          <SettingsSection
            title={t("Services")}
            description={
              accounts.length || missing.length
                ? t(
                    "Local search and scraping are used unless you choose a service. Choose more than one to try them in order.",
                  )
                : undefined
            }
          >
            {[...accounts.map((account) => account.id), ...missing].map(
              (id) => {
                const account = accounts.find((item) => item.id === id);
                const type = account && typeOf(account);
                return (
                  <SettingsRow
                    key={id}
                    label={
                      account ? (
                        <WebProviderIdentity
                          type={account.type}
                          name={account.name}
                        />
                      ) : (
                        nameOf(id)
                      )
                    }
                    description={type?.display_name}
                  >
                    <span className={webStyles.operationChoices}>
                      {(["search", "scrape"] as const).map((operation) => {
                        const checked = chosen[operation].includes(id);
                        const supported =
                          type?.operations.includes(operation) ?? checked;
                        // Keep the column so Search and Scrape stay aligned across rows.
                        if (!supported || (!host[operation] && !checked))
                          return <span key={operation} />;
                        // Restricted scrape needs exactly one supporting service:
                        // choosing another replaces it and it cannot be cleared.
                        const single =
                          operation === "scrape" && restrictedScrape;
                        const restricted =
                          single &&
                          (checked
                            ? chosen.scrape.length === 1
                            : !type?.supports_restricted_scrape);
                        return (
                          <label
                            key={operation}
                            className={webStyles.operationChoice}
                          >
                            <Checkbox
                              aria-label={t(
                                operation === "search"
                                  ? "Use {{name}} for search"
                                  : "Use {{name}} for scraping",
                                { name: account?.name ?? id },
                              )}
                              checked={checked}
                              disabled={restricted}
                              onCheckedChange={(next) =>
                                single && next
                                  ? patch("scrape", {
                                      backend: id,
                                      backend_priority: undefined,
                                    })
                                  : choose(
                                      operation,
                                      next
                                        ? [...chosen[operation], id]
                                        : chosen[operation].filter(
                                            (item) => item !== id,
                                          ),
                                    )
                              }
                            />
                            <span aria-hidden="true">
                              {t(operation === "search" ? "Search" : "Scrape")}
                            </span>
                          </label>
                        );
                      })}
                    </span>
                  </SettingsRow>
                );
              },
            )}
            {!accounts.length && !missing.length && (
              <p className={webStyles.empty}>
                {t(
                  "Uses local DuckDuckGo search and HTML scraping. Add a Web provider to use another service.",
                )}
              </p>
            )}
            <dl className={webStyles.route}>
              {(["search", "scrape"] as const).map(
                (operation) =>
                  host[operation] && (
                    <Fragment key={operation}>
                      <dt>{t(operation === "search" ? "Search" : "Scrape")}</dt>
                      <dd>{route(operation)}</dd>
                    </Fragment>
                  ),
              )}
            </dl>
          </SettingsSection>
        )}
        {host.scrape && restrictedScrape && (
          <p className={webStyles.hint} role="note">
            {t(
              "Scraping is limited to specific domains. Choose one service that supports domain restrictions; local scraping and fallback are unavailable.",
            )}
          </p>
        )}
        {ordered.length > 0 && (
          <details
            className={layout.groupDisclosure}
            open={ordered.some(
              (operation) => !!operations[operation].backend_priority?.length,
            )}
          >
            <summary>
              <span>{t("Fallback order")}</span>
            </summary>
            <div className={webStyles.priority}>
              <p>
                {t(
                  "A single chosen service is used alone. With fallback, services are tried in this order and the local default is always last unless you move it.",
                )}
              </p>
              {ordered.map((operation) => {
                const list = fallbackList(operation);
                const current = operations[operation];
                const move = (position: number, offset: number) => {
                  const next = [...list];
                  [next[position], next[position + offset]] = [
                    next[position + offset],
                    next[position],
                  ];
                  patch(operation, {
                    backend: undefined,
                    backend_priority: next,
                  });
                };
                return (
                  <div key={operation} className={webStyles.priorityGroup}>
                    <h4>{t(operation === "search" ? "Search" : "Scrape")}</h4>
                    {current.backend ? (
                      <Button
                        variant="outline"
                        size="sm"
                        onClick={() =>
                          patch(operation, {
                            backend: undefined,
                            backend_priority: [current.backend!],
                          })
                        }
                      >
                        {t("Fall back to local default")}
                      </Button>
                    ) : (
                      <>
                        {list.map((id, position) => (
                          <div key={id} className={webStyles.priorityRow}>
                            <span>{nameOf(id)}</span>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={t("Move {{name}} up", {
                                name: nameOf(id),
                              })}
                              disabled={position === 0}
                              onClick={() => move(position, -1)}
                            >
                              <ArrowUp />
                            </Button>
                            <Button
                              variant="ghost"
                              size="icon"
                              aria-label={t("Move {{name}} down", {
                                name: nameOf(id),
                              })}
                              disabled={position === list.length - 1}
                              onClick={() => move(position, 1)}
                            >
                              <ArrowDown />
                            </Button>
                          </div>
                        ))}
                        <Button
                          variant="outline"
                          size="sm"
                          onClick={() =>
                            patch(operation, {
                              backend: list.find((id) => id !== LOCAL),
                              backend_priority: undefined,
                            })
                          }
                        >
                          {t("Use only {{name}}", {
                            name: nameOf(list.find((id) => id !== LOCAL)!),
                          })}
                        </Button>
                      </>
                    )}
                  </div>
                );
              })}
            </div>
          </details>
        )}
        <Link to="/settings/connections">{t("Manage Web providers")}</Link>
        <ErrorNotice error={catalog.error} />
      </div>
    </details>
  );
}
