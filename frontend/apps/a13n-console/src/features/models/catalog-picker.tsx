import { SlidersHorizontalIcon } from "@phosphor-icons/react";
import { Button, ChoiceField, Input } from "a13n-ui";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import {
  DirectoryEmpty,
  DirectoryGroup,
  DirectoryList,
  DirectoryRow,
} from "../../shared/dialogs";
import { ModelIcon } from "./model-icon";
import styles from "./models.module.css";

type Entry = Schema["CatalogModel"];
type Ref = Schema["CatalogRef"];
const MAX_VISIBLE_MODELS = 100;
export const catalogRefKey = (ref: Ref) =>
  JSON.stringify([ref.provider, ref.model]);

/** One row per model identity; regional and gateway copies stay behind it. */
export function groupCatalog(entries: readonly Entry[]) {
  const groups = new Map<string, Entry[]>();
  for (const entry of entries) {
    const group = groups.get(entry.identity);
    if (group) group.push(entry);
    else groups.set(entry.identity, [entry]);
  }
  return [...groups.values()].sort((a, b) =>
    a[0].name.localeCompare(b[0].name),
  );
}

/**
 * The model step of the add flow: search the catalog, pick a model, and resolve
 * the concrete provider variant when one identity is served several ways.
 */
export function CatalogPicker({
  entries,
  channels,
  allowCompatible,
  providerName,
  value,
  onSelect,
}: {
  entries: readonly Entry[];
  channels: readonly string[];
  allowCompatible: boolean;
  providerName?: string;
  value: Ref | null;
  /** `null` chooses a custom model with no catalog identity. */
  onSelect: (entry: Entry | null) => void;
}) {
  const { t } = useTranslation();
  const [expanded, setExpanded] = useState<boolean>();
  const [pending, setPending] = useState<Entry[]>();
  const [query, setQuery] = useState("");
  const compatible =
    allowCompatible &&
    (expanded ?? (!!value && !channels.includes(value.provider)));
  const native = useMemo(
    () => entries.filter((entry) => channels.includes(entry.ref.provider)),
    [entries, channels],
  );
  const groups = useMemo(
    () => groupCatalog(compatible ? entries : native),
    [compatible, entries, native],
  );
  const search = query.trim().toLocaleLowerCase();
  const matches = useMemo(
    () =>
      search
        ? groups.filter((group) =>
            group.some((entry) =>
              [entry.name, entry.identity, entry.ref.model, entry.provider_name]
                .join(" ")
                .toLocaleLowerCase()
                .includes(search),
            ),
          )
        : groups,
    [groups, search],
  );
  const visible = matches.slice(0, MAX_VISIBLE_MODELS);
  const servedHere = (group: Entry[]) =>
    group.some((entry) => channels.includes(entry.ref.provider));
  const provided = visible.filter(servedHere);
  const other = visible.filter((group) => !servedHere(group));
  const selectedKey = value ? catalogRefKey(value) : null;

  function choose(group: Entry[]) {
    const versions = group.filter((item) =>
      channels.includes(item.ref.provider),
    );
    const official = group.find(
      (item) => `${item.ref.provider}/${item.ref.model}` === item.identity,
    );
    const variants = versions.length ? versions : [official ?? group[0]];
    if (variants.length === 1) {
      setPending(undefined);
      onSelect(variants[0]);
      return;
    }
    setPending(variants);
  }
  function rows(group: Entry[]) {
    return (
      <DirectoryRow
        key={group[0].identity}
        icon={<ModelIcon upstream={group[0].identity} size={20} />}
        name={group[0].name}
        detail={group[0].identity}
        meta={
          group.some((item) => catalogRefKey(item.ref) === selectedKey)
            ? t("Selected")
            : undefined
        }
        onClick={() => choose(group)}
      />
    );
  }
  return (
    <div className={styles.step}>
      {pending && (
        <div className={styles.variantChoice}>
          <ChoiceField
            label={t("Provider model variant")}
            placeholder={t("Choose a model…")}
            description={t("This model is served under more than one ID.")}
            value=""
            options={pending.map((item) => ({
              value: catalogRefKey(item.ref),
              label: `${item.provider_name} · ${item.ref.model}`,
            }))}
            onValueChange={(key) => {
              const item = pending.find(
                (entry) => catalogRefKey(entry.ref) === key,
              );
              if (!item) return;
              setPending(undefined);
              onSelect(item);
            }}
          />
        </div>
      )}
      <DirectoryList
        search={
          <Input
            type="search"
            autoFocus
            aria-label={t("Search models…")}
            placeholder={t("Search models…")}
            value={query}
            onChange={(event) => setQuery(event.target.value)}
          />
        }
        footer={
          <div className={styles.catalogFooter}>
            <span>
              {matches.length > MAX_VISIBLE_MODELS
                ? t("Showing first 100 models. Search to narrow the list.")
                : ""}
            </span>
            {allowCompatible && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => setExpanded(!compatible)}
              >
                {t(
                  compatible
                    ? "Show provider models"
                    : "Other models (compatible)…",
                )}
              </Button>
            )}
          </div>
        }
      >
        {provided.length > 0 && (
          <DirectoryGroup
            label={
              providerName
                ? t("Models from {{provider}}", { provider: providerName })
                : t("Models")
            }
          >
            {provided.map(rows)}
          </DirectoryGroup>
        )}
        {other.length > 0 && (
          <DirectoryGroup label={t("Other models (compatible)")}>
            {other.map(rows)}
          </DirectoryGroup>
        )}
        {!visible.length && (
          <DirectoryEmpty>
            {t("No models found. You can still add a model manually.")}
          </DirectoryEmpty>
        )}
        <DirectoryGroup label={t("Not listed")}>
          <DirectoryRow
            icon={<SlidersHorizontalIcon size={18} aria-hidden="true" />}
            name={t("Custom model")}
            detail={t("Enter an upstream model ID")}
            onClick={() => {
              setPending(undefined);
              onSelect(null);
            }}
          />
        </DirectoryGroup>
      </DirectoryList>
    </div>
  );
}
