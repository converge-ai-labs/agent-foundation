import { Button, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorNotice } from "../../shared/feedback";
import { TextAreaField } from "../../shared/forms";
import { sessionConfiguration } from "../../shared/forms/validation";
import shared from "../../shared/shared.module.css";
import styles from "./credentials.module.css";
import type { SessionPolicy } from "./session-policy";

/** Retains access to less common Session options without a second editable source. */
export function SessionPolicyJson({
  value,
  onChange,
}: {
  value: SessionPolicy;
  onChange: (value: SessionPolicy) => void;
}) {
  const { t } = useTranslation();
  const [open, setOpen] = useState(false);
  return (
    <ModalFrame
      open={open}
      onOpenChange={setOpen}
      title={t("Session configuration JSON")}
      closeLabel={t("Close")}
      description={t(
        "Edit all Session settings. Apply updates the draft; save the parent form to publish changes.",
      )}
      trigger={
        <Button
          className={styles.inlineAction}
          type="button"
          size="sm"
          variant="ghost"
        >
          {t("Edit Session JSON")}
        </Button>
      }
    >
      {open && (
        <JsonDraft
          value={value}
          onApply={(next) => {
            onChange(next);
            setOpen(false);
          }}
          onClose={() => setOpen(false)}
        />
      )}
    </ModalFrame>
  );
}

function JsonDraft({
  value,
  onApply,
  onClose,
}: {
  value: SessionPolicy;
  onApply: (value: SessionPolicy) => void;
  onClose: () => void;
}) {
  const { t } = useTranslation();
  const [text, setText] = useState(JSON.stringify(value, null, 2));
  const [error, setError] = useState<Error>();
  return (
    <div className={shared.stack}>
      <TextAreaField
        label={t("Session configuration JSON")}
        value={text}
        onChange={setText}
        code
        rows={14}
      />
      <ErrorNotice error={error} />
      <div className={styles.actions}>
        <Button type="button" variant="outline" onClick={onClose}>
          {t("Cancel")}
        </Button>
        <Button
          type="button"
          onClick={() => {
            try {
              onApply(sessionConfiguration(text));
            } catch (error) {
              setError(
                error instanceof Error
                  ? error
                  : new Error(t("Invalid configuration")),
              );
            }
          }}
        >
          {t("Apply to draft")}
        </Button>
      </div>
    </div>
  );
}
