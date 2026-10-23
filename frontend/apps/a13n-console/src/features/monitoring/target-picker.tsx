import {
  CaretDownIcon,
  CheckIcon,
  DesktopIcon,
  StackIcon,
} from "@phosphor-icons/react";
import {
  Button,
  ChoiceField,
  Input,
  Popover,
  PopoverPopup,
  PopoverTrigger,
} from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import type { Source } from "./api";
import { statusLabels } from "./labels";
import styles from "./monitoring.module.css";

export type TargetOption = {
  value: string;
  name: string;
  kind: "environment" | "device";
  status: string;
  source: string;
  labels: Record<string, string>;
  deviceId: string;
};
export function targetInventory(
  environments: Schema["EnvironmentView"][],
  devices: Schema["Device"][],
): TargetOption[] {
  return [
    ...environments.map((item) => ({
      value: `environment:${item.id}`,
      name: item.name,
      kind: "environment" as const,
      status: item.status,
      source: item.metrics_data_source_id ?? "",
      labels: item.metrics_labels ?? {},
      deviceId: item.device_id ?? "",
    })),
    ...devices.map((item) => ({
      value: `device:${item.id}`,
      name: item.name,
      kind: "device" as const,
      status: item.enabled ? "enabled" : "disabled",
      source: item.metrics_data_source_id ?? "",
      labels: {},
      deviceId: item.device_id,
    })),
  ];
}
export function matchesTarget(
  item: TargetOption,
  filters: { search: string; kind: string; status: string; source: string },
) {
  const text = [
    item.name,
    item.value,
    item.deviceId,
    ...Object.entries(item.labels).map(([key, value]) => `${key}=${value}`),
  ]
    .join(" ")
    .toLowerCase();
  return (
    (!filters.kind || item.kind === filters.kind) &&
    (!filters.status || item.status === filters.status) &&
    (!filters.source ||
      (filters.source === "none"
        ? !item.source
        : item.source === filters.source)) &&
    filters.search
      .toLowerCase()
      .trim()
      .split(/\s+/)
      .every((word) => text.includes(word))
  );
}
const noFilters = { search: "", kind: "", status: "", source: "" };
/** A searchable popover over every Environment and Device in the workspace. */
export function TargetPicker({
  items,
  sources,
  value,
  onChange,
}: {
  items: TargetOption[];
  sources: Source[];
  value: string;
  onChange: (value: string) => void;
}) {
  const { t } = useTranslation();
  const statuses = statusLabels(t);
  const [open, setOpen] = useState(false);
  const [filters, setFilters] = useState(noFilters);
  const selected = items.find((item) => item.value === value);
  const visible = items.filter((item) => matchesTarget(item, filters));
  const sourceName = (id: string) =>
    sources.find((source) => source.id === id)?.name ??
    (id || t("No data source"));
  const name =
    selected?.name ?? (value ? t("Unavailable target") : t("Select target"));
  return (
    <Popover
      open={open}
      onOpenChange={(next) => {
        setOpen(next);
        if (!next) setFilters(noFilters);
      }}
    >
      <PopoverTrigger
        render={
          <Button
            variant="outline"
            className={styles.targetTrigger}
            aria-label={`${t("Target")}: ${name}`}
          />
        }
      >
        <span className={styles.triggerLabel}>{t("Target")}</span>
        {selected &&
          (selected.kind === "device" ? (
            <DesktopIcon aria-hidden="true" />
          ) : (
            <StackIcon aria-hidden="true" />
          ))}
        <span className={styles.targetName}>{name}</span>
        <CaretDownIcon
          size={16}
          aria-hidden="true"
          className={styles.targetCaret}
        />
      </PopoverTrigger>
      <PopoverPopup
        aria-label={t("Select target")}
        align="start"
        className={styles.targetPopup}
      >
        <div className={styles.targetSearch}>
          <Input
            type="search"
            size="sm"
            autoFocus
            aria-label={t("Search targets")}
            placeholder={t("Search name, ID or label (region=west)…")}
            value={filters.search}
            onChange={(event) =>
              setFilters({ ...filters, search: event.target.value })
            }
          />
          <div className={styles.targetFilters}>
            <ChoiceField
              label={t("Type")}
              variant="filter"
              value={filters.kind}
              options={[
                { value: "", label: t("All") },
                { value: "environment", label: t("Environments") },
                { value: "device", label: t("Devices") },
              ]}
              onValueChange={(kind) => setFilters({ ...filters, kind })}
            />
            <ChoiceField
              label={t("Status")}
              variant="filter"
              value={filters.status}
              options={[
                { value: "", label: t("All") },
                ...[...new Set(items.map((item) => item.status))]
                  .sort()
                  .map((status) => ({
                    value: status,
                    label: statuses[status] ?? status,
                  })),
              ]}
              onValueChange={(status) => setFilters({ ...filters, status })}
            />
            <ChoiceField
              label={t("Data source")}
              variant="filter"
              value={filters.source}
              options={[
                { value: "", label: t("All") },
                { value: "none", label: t("No data source") },
                ...sources.map((source) => ({
                  value: source.id,
                  label: source.name,
                })),
              ]}
              onValueChange={(source) => setFilters({ ...filters, source })}
            />
          </div>
        </div>
        <div
          className={`${styles.targetList} a13n-scrollbar`}
          aria-label={t("Targets")}
        >
          {visible.map((item) => (
            <button
              type="button"
              key={item.value}
              className={styles.targetRow}
              aria-pressed={item.value === value}
              onClick={() => {
                onChange(item.value);
                setOpen(false);
                setFilters(noFilters);
              }}
            >
              {item.kind === "device" ? (
                <DesktopIcon size={16} aria-hidden="true" />
              ) : (
                <StackIcon size={16} aria-hidden="true" />
              )}
              <span>
                <strong>{item.name}</strong>
                <small>
                  {[
                    t(item.kind === "device" ? "Device" : "Environment"),
                    statuses[item.status] ?? item.status,
                    sourceName(item.source),
                    ...Object.entries(item.labels).map(
                      ([key, label]) => `${key}=${label}`,
                    ),
                  ].join(" · ")}
                </small>
              </span>
              {item.value === value && (
                <CheckIcon size={14} aria-hidden="true" />
              )}
            </button>
          ))}
          {!visible.length && (
            <p className={styles.targetEmpty}>{t("No matching targets")}</p>
          )}
        </div>
        <div className={styles.targetFooter}>
          <span>
            {t("{{shown}} of {{count}} targets", {
              shown: visible.length,
              count: items.length,
            })}
          </span>
          {Object.values(filters).some(Boolean) && (
            <Button
              variant="ghost"
              size="xs"
              onClick={() => setFilters(noFilters)}
            >
              {t("Clear filters")}
            </Button>
          )}
        </div>
      </PopoverPopup>
    </Popover>
  );
}
