import { SlidersHorizontalIcon } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { mediaKinds } from "../../models/media-understanding-fields";
import {
  RunOptionsDialog,
  type OptionField,
  type RunOptionsState,
} from "./options-dialog";
import styles from "./composer.module.css";

/**
 * Overrides stay visible as chips on the composer instead of hiding behind a
 * dialog: each chip names what it changed and opens that field.
 */
export function RunOptions({
  options,
  showAgent = true,
}: {
  options: RunOptionsState;
  showAgent?: boolean;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState<OptionField>();
  function edit(field?: OptionField) {
    setFocus(field);
    setOpen(true);
  }
  return (
    <>
      <OptionChip onClick={() => edit()} label={t("Options")}>
        <SlidersHorizontalIcon size={13} aria-hidden="true" />
        {t("Options")}
      </OptionChip>
      {showAgent && options.agent && (
        <OptionChip
          onClick={() => edit("agent")}
          label={t("Agent")}
          value={options.labels.agent ?? options.agent}
        />
      )}
      {options.model && (
        <OptionChip
          onClick={() => edit("model")}
          label={t("Model")}
          value={options.labels.model ?? options.model}
        />
      )}
      {options.environment !== "inherit" && (
        <OptionChip
          onClick={() => edit("environment")}
          label={t("Environment")}
          value={options.labels.environment ?? options.environment}
        />
      )}
      {mediaKinds.map(({ kind, label }) => {
        const key = options.mediaUnderstanding[kind];
        return key ? (
          <OptionChip
            key={kind}
            onClick={() => edit(kind)}
            label={t(label)}
            value={options.labels.media?.[kind] ?? key}
          />
        ) : null;
      })}
      {options.overrideInstructions && (
        <OptionChip
          onClick={() => edit("instructions")}
          label={t("Instructions overridden")}
        >
          {t("Instructions overridden")}
        </OptionChip>
      )}
      <RunOptionsDialog
        options={options}
        showAgent={showAgent}
        open={open}
        onOpenChange={setOpen}
        focus={focus}
      />
    </>
  );
}

function OptionChip({
  label,
  value,
  onClick,
  children,
}: {
  label: string;
  value?: string;
  onClick: () => void;
  children?: ReactNode;
}) {
  return (
    <button
      type="button"
      className={styles.chip}
      onClick={onClick}
      aria-label={value ? `${label}: ${value}` : label}
      title={value ? `${label}: ${value}` : label}
    >
      {children ?? (
        <>
          <span className={styles.chipLabel}>{label}</span>
          <span className={styles.chipValue}>{value}</span>
        </>
      )}
    </button>
  );
}
