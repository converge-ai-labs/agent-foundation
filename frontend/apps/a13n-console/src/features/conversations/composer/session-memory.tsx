import { BrainIcon, CaretDownIcon } from "@phosphor-icons/react";
import {
  Button,
  Popover,
  PopoverPopup,
  PopoverTitle,
  PopoverTrigger,
  SegmentedControl,
} from "a13n-ui";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../shared/api";
import {
  defaultSessionMemory,
  SessionMemoryFields,
  validSpaceSelection,
} from "../../memories/session-settings";
import filterStyles from "../../../shared/forms/filter-popover.module.css";
import styles from "./composer.module.css";

type Settings = Schema["SessionMemorySettings"];

export function SessionMemoryControl({
  value,
  onChange,
  locked,
}: {
  value: Settings | null;
  onChange: (value: Settings | null) => void;
  locked: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const custom = useRef<Settings>(defaultSessionMemory);
  const invalid = value !== null && !validSpaceSelection(value);
  const summary = invalid
    ? t("Review settings")
    : value === null
      ? t("Follow agent")
      : !value.enabled
        ? t("Off")
        : t("Custom · {{count}} spaces", { count: value.spaces?.length ?? 0 });
  return (
    <Popover open={open} onOpenChange={setOpen}>
      <PopoverTrigger className={styles.chip}>
        <BrainIcon size={14} aria-hidden="true" />
        <span className={styles.chipLabel}>{t("Memory")}</span>
        <span aria-hidden="true">·</span>
        <span className={styles.chipValue}>{summary}</span>
        <CaretDownIcon size={12} aria-hidden="true" />
      </PopoverTrigger>
      <PopoverPopup
        side="top"
        align="start"
        sideOffset={8}
        className={`${styles.memoryPopup} ${filterStyles.popup}`}
      >
        <div className={filterStyles.body}>
          <header className={styles.memoryHeader}>
            <PopoverTitle>{t("Session memory")}</PopoverTitle>
          </header>
          <SegmentedControl
            label={t("Memory configuration")}
            value={value === null ? "inherit" : "custom"}
            className={styles.memoryModes}
            options={[
              { value: "inherit", label: t("Follow agent"), disabled: locked },
              { value: "custom", label: t("Custom"), disabled: locked },
            ]}
            onValueChange={(mode) => {
              if (mode === "inherit") {
                if (value !== null) custom.current = value;
                onChange(null);
              } else onChange(custom.current);
            }}
          />
          <p className={styles.memoryDescription}>
            {locked
              ? t(
                  "This session has already been created. Retry with the same memory settings.",
                )
              : value === null
                ? t(
                    "Uses the first agent's memory settings when this session starts.",
                  )
                : t(
                    "Configure this session without changing the agent's defaults.",
                  )}
          </p>
          {value !== null && (
            <SessionMemoryFields
              value={value}
              onChange={onChange}
              disabled={locked}
              compact
            />
          )}
          <div className={styles.memoryFooter}>
            <Button
              type="button"
              variant="outline"
              size="sm"
              disabled={invalid}
              onClick={() => setOpen(false)}
            >
              {t("Done")}
            </Button>
          </div>
        </div>
      </PopoverPopup>
    </Popover>
  );
}
