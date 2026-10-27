import { SlidersHorizontalIcon } from "@phosphor-icons/react";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import {
  RunOptionsDialog,
  type OptionField,
  type RunOptionsState,
  type RunOptionValues,
} from "./options-dialog";
import styles from "./composer.module.css";

/**
 * One quiet settings entry; new conversations also name their persistent setup.
 */
export function RunOptions({
  options,
  showAgent = true,
  newThread = true,
  agentId,
}: {
  options: RunOptionsState;
  showAgent?: boolean;
  newThread?: boolean;
  agentId?: string;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  const [focus, setFocus] = useState<OptionField>();
  // The choices the dialog opened with, restored when it is cancelled.
  const [saved, setSaved] = useState<RunOptionValues>();
  function edit(field?: OptionField) {
    setSaved(options.values);
    setFocus(field);
    setOpen(true);
  }
  return (
    <>
      <OptionChip onClick={() => edit()} label={t("Run options")}>
        <SlidersHorizontalIcon size={13} aria-hidden="true" />
        {t("Run options")}
        {options.modified && (
          <span
            className={styles.modifiedDot}
            role="img"
            aria-label={t("Settings modified")}
          />
        )}
      </OptionChip>
      {showAgent && options.agent && (
        <OptionChip
          onClick={() => edit("agent")}
          label={t("Agent")}
          value={options.labels.agent ?? options.agent}
        />
      )}
      {newThread && options.environment !== "inherit" && (
        <OptionChip
          onClick={() => edit("environment")}
          label={t("Environment")}
          value={options.labels.environment ?? options.environment}
        />
      )}
      {newThread && options.memories.length > 0 && (
        <OptionChip
          onClick={() => edit("memories")}
          label={t("Memories")}
          value={options.memories.map((mount) => mount.name).join(", ")}
        />
      )}
      <RunOptionsDialog
        options={options}
        showAgent={showAgent}
        newThread={newThread}
        agentId={agentId}
        open={open}
        onOpenChange={setOpen}
        onCancel={() => {
          if (saved) options.restore(saved);
          setOpen(false);
        }}
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
