import { Button, Input } from "a13n-ui";
import { useTranslation } from "react-i18next";
import styles from "./page.module.css";

/**
 * Draft editors surface their pending change in a floating bar instead of
 * implying autosave: what changed, what saving will do, discard, and save.
 */
export function SaveBar({
  title,
  consequence,
  note,
  onNoteChange,
  noteLabel,
  notePlaceholder,
  discardLabel,
  onDiscard,
  saveLabel,
  pending = false,
  disabled = false,
}: {
  title: string;
  consequence?: string;
  note?: string;
  onNoteChange?: (value: string) => void;
  noteLabel?: string;
  notePlaceholder?: string;
  discardLabel?: string;
  onDiscard?: () => void;
  saveLabel: string;
  pending?: boolean;
  disabled?: boolean;
}) {
  const { t } = useTranslation();
  return (
    <div className={styles.saveBar} role="region" aria-label={title}>
      <div className={styles.saveStatus}>
        <span className={styles.savePulse} aria-hidden="true" />
        <span>
          {title}
          {consequence && <small>{consequence}</small>}
        </span>
      </div>
      {onNoteChange && (
        <div className={styles.saveNote}>
          <Input
            size="sm"
            aria-label={noteLabel ?? t("Note")}
            placeholder={notePlaceholder ?? noteLabel}
            value={note ?? ""}
            maxLength={280}
            onChange={(event) => onNoteChange(event.target.value)}
          />
        </div>
      )}
      <div className={styles.saveActions}>
        {onDiscard && (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            disabled={pending}
            onClick={onDiscard}
          >
            {discardLabel ?? t("Discard")}
          </Button>
        )}
        <Button
          type="submit"
          size="sm"
          disabled={pending || disabled}
          loading={pending}
        >
          {saveLabel}
        </Button>
      </div>
    </div>
  );
}
