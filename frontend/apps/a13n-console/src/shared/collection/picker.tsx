import {
  Button,
  Checkbox,
  Input,
  Popover,
  PopoverPopup,
  PopoverTrigger,
} from "a13n-ui";
import { PlusIcon } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import styles from "./collection.module.css";

export interface PickerItem {
  id: string;
  name: string;
  detail?: string;
  icon?: ReactNode;
  disabled?: boolean;
  disabledReason?: string;
}

/**
 * Popover picker used to attach workspace resources. Selection rows keep the
 * collection row anatomy with a checkbox in place of the tile.
 */
export function ResourcePicker({
  label,
  searchLabel,
  emptyLabel,
  items,
  selected,
  onToggle,
  manageHref,
  manageLabel,
  loading,
}: {
  label: string;
  searchLabel: string;
  emptyLabel: string;
  items: PickerItem[];
  selected: Set<string>;
  onToggle: (id: string, checked: boolean) => void;
  manageHref: string;
  manageLabel: string;
  loading?: boolean;
}) {
  const { t } = useTranslation();
  const [query, setQuery] = useState("");
  const visible = items
    .filter((item) =>
      `${item.name} ${item.detail ?? ""}`
        .toLocaleLowerCase()
        .includes(query.toLocaleLowerCase()),
    )
    .sort((a, b) => Number(selected.has(b.id)) - Number(selected.has(a.id)));
  return (
    <Popover onOpenChange={(open) => !open && setQuery("")}>
      <PopoverTrigger render={<Button variant="outline" size="sm" />}>
        <PlusIcon size={14} />
        {label}
      </PopoverTrigger>
      <PopoverPopup align="start" className={styles.picker}>
        <div className={styles.pickerSearch}>
          <Input
            type="search"
            size="sm"
            aria-label={searchLabel}
            placeholder={searchLabel}
            value={query}
            autoFocus
            onChange={(event) => setQuery(event.target.value)}
          />
        </div>
        <div className={`a13n-scrollbar ${styles.pickerList}`}>
          {visible.map((item) => (
            <label
              key={item.id}
              className={styles.pickerItem}
              aria-disabled={item.disabled || undefined}
              title={item.disabled ? item.disabledReason : undefined}
            >
              <Checkbox
                checked={selected.has(item.id)}
                disabled={item.disabled}
                onCheckedChange={(value) => onToggle(item.id, value === true)}
              />
              <span className={styles.pickerCopy}>
                <strong>{item.name}</strong>
                {item.detail && <small>{item.detail}</small>}
              </span>
              {item.icon}
            </label>
          ))}
          {!visible.length && (
            <p className={styles.pickerEmpty}>
              {loading ? t("Loading…") : query ? t("No matches") : emptyLabel}
            </p>
          )}
        </div>
        <div className={styles.pickerFooter}>
          <span className="text-muted-foreground">
            {t("{{count}} selected", { count: selected.size })}
          </span>
          <Link to={manageHref}>{manageLabel}</Link>
        </div>
      </PopoverPopup>
    </Popover>
  );
}
