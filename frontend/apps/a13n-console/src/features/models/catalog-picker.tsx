import { Button, ChoiceField, SearchPicker } from "a13n-ui";
import { useMemo, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { ModelIcon } from "./model-icon";

type Entry = Schema["CatalogModel"];
type Ref = Schema["CatalogRef"];
const MAX_VISIBLE_MODELS = 100;
export const catalogRefKey = (ref: Ref) =>
  JSON.stringify([ref.provider, ref.model]);

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

export function CatalogPicker({
  entries,
  channels,
  allowCompatible,
  value,
  onSelect,
  onPendingChange,
}: {
  entries: readonly Entry[];
  channels: readonly string[];
  allowCompatible: boolean;
  value: Ref | null;
  onSelect: (entry: Entry | null) => void;
  onPendingChange: (pending: boolean) => void;
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
  const nameCounts = useMemo(() => {
    const counts = new Map<string, number>();
    for (const group of groups)
      counts.set(group[0].name, (counts.get(group[0].name) ?? 0) + 1);
    return counts;
  }, [groups]);
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
  const visibleGroups = matches.slice(0, MAX_VISIBLE_MODELS);
  const selected = entries.find(
    (entry) => value && catalogRefKey(entry.ref) === catalogRefKey(value),
  );
  const selectedValue =
    pending?.[0].identity ??
    selected?.identity ??
    (value ? catalogRefKey(value) : "custom");
  const variants =
    pending ?? native.filter((item) => item.identity === selected?.identity);
  function choose(identity: string) {
    setPending(undefined);
    onPendingChange(false);
    if (identity === "custom") {
      onSelect(null);
      return;
    }
    const group = groups.find((items) => items[0].identity === identity);
    if (!group) return;
    const nativeVersions = group.filter((item) =>
      channels.includes(item.ref.provider),
    );
    const official = group.find(
      (item) => `${item.ref.provider}/${item.ref.model}` === item.identity,
    );
    const versions = nativeVersions.length
      ? nativeVersions
      : [official ?? group[0]];
    if (versions.length === 1) onSelect(versions[0]);
    else {
      setPending(versions);
      onPendingChange(true);
    }
  }
  return (
    <>
      <SearchPicker
        label={t("Model")}
        placeholder={t("Choose a model…")}
        emptyMessage={t("No models found. You can still add a model manually.")}
        value={selectedValue}
        onValueChange={choose}
        onSearchChange={setQuery}
        groups={[
          {
            label: t("Models"),
            options: [
              {
                value: "custom",
                label: t("Custom model"),
                description: t("Enter an upstream model ID"),
                icon: <ModelIcon upstream="" size={20} />,
              },
              ...(value &&
              !visibleGroups.some(
                (group) => group[0].identity === selectedValue,
              ) &&
              !pending
                ? [
                    {
                      value: selectedValue,
                      label: selected?.name ?? value.model,
                      icon: <ModelIcon upstream={value.model} size={20} />,
                    },
                  ]
                : []),
              ...visibleGroups.map((group) => ({
                value: group[0].identity,
                label: group[0].name,
                keywords: group.flatMap((entry) => [
                  entry.identity,
                  entry.ref.model,
                  entry.provider_name,
                ]),
                description:
                  (nameCounts.get(group[0].name) ?? 0) > 1
                    ? group[0].identity
                    : undefined,
                icon: <ModelIcon upstream={group[0].identity} size={20} />,
              })),
            ],
          },
        ]}
        footer={
          (allowCompatible || matches.length > MAX_VISIBLE_MODELS) && (
            <div>
              {matches.length > MAX_VISIBLE_MODELS && (
                <p className="px-2 py-1 text-xs text-muted-foreground">
                  {t("Showing first 100 models. Search to narrow the list.")}
                </p>
              )}
              {allowCompatible && (
                <Button
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
          )
        }
      />
      {variants.length > 1 && (
        <ChoiceField
          label={t("Provider model variant")}
          placeholder={t("Choose a model…")}
          value={pending || !value ? "" : catalogRefKey(value)}
          options={variants.map((item) => ({
            value: catalogRefKey(item.ref),
            label: `${item.provider_name} · ${item.ref.model}`,
          }))}
          onValueChange={(key) => {
            const item = variants.find(
              (item) => catalogRefKey(item.ref) === key,
            );
            if (item) {
              onSelect(item);
              setPending(undefined);
              onPendingChange(false);
            }
          }}
        />
      )}
    </>
  );
}
