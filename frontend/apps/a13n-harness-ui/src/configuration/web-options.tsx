import { Link } from "react-router";
import { Button } from "a13n-ui";
import { ArrowUp, ArrowDown, X } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { useWebProviders } from "../transport/context";
import { ErrorNotice } from "../shell/ui";
import { SettingsChoice, SettingsToggle } from "../shell/settings";
import { ResourceChoice } from "./resource-choice";
import { readDocument } from "./documents";
import { WebProviderIdentity } from "./web-providers";
import styles from "../shell/workbench.module.css";
import webStyles from "./web-providers.module.css";

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
  return (
    <details className={styles.details}>
      <summary>
        <span>{t("Web options")}</span>
      </summary>
      <div className={styles.stack}>
        {(["search", "scrape"] as const).map((operation) => {
          const current = config[operation] ?? {};
          const mode =
            current.mode ?? (operation === "search" ? "auto" : "host");
          const priority: string[] = current.backend_priority ?? [];
          const options = [
            {
              value: "default",
              label: t(
                operation === "search"
                  ? "Local DuckDuckGo search"
                  : "Local HTML scraping",
              ),
            },
            ...(catalog.data?.accounts ?? [])
              .filter((account) =>
                catalog.data?.types.some(
                  (type) =>
                    type.type === account.type &&
                    type.operations.includes(operation),
                ),
              )
              .map((account) => ({
                value: account.id,
                label: account.name,
                disabled:
                  operation === "scrape" &&
                  !!(
                    current.allow_domains?.length ||
                    current.deny_domains?.length
                  ) &&
                  !catalog.data?.types.find(
                    (type) => type.type === account.type,
                  )?.supports_restricted_scrape,
                icon: <WebProviderIdentity type={account.type} name="" />,
              })),
          ];
          const selected =
            current.backend ?? (priority.length ? "priority" : "");
          const setPriority = (next: string[]) =>
            patch(operation, {
              backend: undefined,
              backend_priority: next.length ? next : undefined,
            });
          return (
            <div key={operation} className={styles.stack}>
              <SettingsChoice
                label={t(
                  operation === "search" ? "Web search" : "Web scraping",
                )}
                value={mode}
                options={
                  operation === "search"
                    ? [
                        { value: "auto", label: t("Automatic") },
                        { value: "host", label: t("Host search") },
                        {
                          value: "native",
                          label: t("Model-native search"),
                          disabled: !!(
                            current.allow_domains?.length ||
                            current.deny_domains?.length
                          ),
                        },
                        { value: "off", label: t("Off") },
                      ]
                    : [
                        { value: "host", label: t("Host scraping") },
                        { value: "off", label: t("Off") },
                      ]
                }
                onValueChange={(next) =>
                  patch(operation, {
                    mode: next,
                    ...(next === "off" || next === "native"
                      ? { backend: undefined, backend_priority: undefined }
                      : {}),
                  })
                }
              />
              {mode !== "off" && mode !== "native" && (
                <>
                  <ResourceChoice
                    row
                    label={t(
                      operation === "search"
                        ? "Search backend"
                        : "Scrape backend",
                    )}
                    value={selected}
                    options={[
                      { value: "", label: t("Local default") },
                      ...options,
                      { value: "priority", label: t("Ordered fallback") },
                    ]}
                    onValueChange={(next) =>
                      patch(
                        operation,
                        next === "priority"
                          ? {
                              backend: undefined,
                              backend_priority: ["default"],
                            }
                          : {
                              backend: next || undefined,
                              backend_priority: undefined,
                            },
                      )
                    }
                  />
                  {priority.length > 0 && (
                    <div className={webStyles.priority}>
                      <p>
                        {t(
                          "Try these in order, then any remaining bound local backend. Unselected accounts are never added automatically.",
                        )}
                      </p>
                      {priority.map((id, position) => (
                        <div key={id} className={webStyles.priorityRow}>
                          <span>
                            {options.find((item) => item.value === id)?.label ??
                              id}
                          </span>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={t("Move backend up")}
                            disabled={position === 0}
                            onClick={() => {
                              const next = [...priority];
                              [next[position - 1], next[position]] = [
                                next[position],
                                next[position - 1],
                              ];
                              setPriority(next);
                            }}
                          >
                            <ArrowUp />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={t("Move backend down")}
                            disabled={position === priority.length - 1}
                            onClick={() => {
                              const next = [...priority];
                              [next[position], next[position + 1]] = [
                                next[position + 1],
                                next[position],
                              ];
                              setPriority(next);
                            }}
                          >
                            <ArrowDown />
                          </Button>
                          <Button
                            variant="ghost"
                            size="icon"
                            aria-label={t("Remove backend")}
                            onClick={() =>
                              setPriority(
                                priority.filter((item) => item !== id),
                              )
                            }
                          >
                            <X />
                          </Button>
                        </div>
                      ))}
                      <ResourceChoice
                        row
                        label={t("Add fallback backend")}
                        value=""
                        options={[
                          { value: "", label: t("Choose a backend") },
                          ...options.filter(
                            (item) => !priority.includes(item.value),
                          ),
                        ]}
                        onValueChange={(next) => {
                          if (next) setPriority([...priority, next]);
                        }}
                      />
                    </div>
                  )}
                </>
              )}
            </div>
          );
        })}
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
        <Link to="/settings/connections">{t("Manage Web providers")}</Link>
        <ErrorNotice error={catalog.error} />
      </div>
    </details>
  );
}
