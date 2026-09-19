import { Button, SettingsRow } from "a13n-ui";
import { useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import styles from "./providers.module.css";

/** What the editor can say about a stored secret right now. */
export type CredentialRowState =
  "configured" | "replacing" | "removing" | "not_configured";

export function credentialRowState({
  configured,
  editing,
  removing,
}: {
  configured: boolean;
  editing: boolean;
  removing: boolean;
}): CredentialRowState {
  if (editing) return "replacing";
  if (removing) return "removing";
  return configured ? "configured" : "not_configured";
}

/**
 * The one row every provider editor uses for its secret: what the service
 * holds today at the right, and the inputs only once you ask for them.
 */
export function CredentialRow({
  label,
  configured,
  removing = false,
  onRemovingChange,
  onDiscard,
  children,
}: {
  /** Named by the credential schema: "API key", "Token", "Token ID and secret". */
  label: string;
  configured: boolean;
  removing?: boolean;
  /** Omitted where the category cannot clear a stored secret. */
  onRemovingChange?: (removing: boolean) => void;
  /** Clears the drafted secret when the saved one is kept after all. */
  onDiscard: () => void;
  /**
   * The secret inputs, revealed while replacing or adding. They carry their
   * own labels and key link, exactly as the connect step presents them.
   */
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [editing, setEditing] = useState(false);
  const state = credentialRowState({ configured, editing, removing });
  function close() {
    setEditing(false);
    onDiscard();
  }
  function open() {
    onRemovingChange?.(false);
    setEditing(true);
  }
  if (state === "replacing")
    return (
      <div className={styles.credentialEdit} data-state={state}>
        {children}
        {configured && (
          <button
            type="button"
            className={styles.credentialKeep}
            onClick={close}
          >
            {t("Keep the saved key")}
          </button>
        )}
      </div>
    );
  return (
    <SettingsRow label={label} stackOnNarrow={false}>
      <div className={styles.credentialState} data-state={state}>
        <span
          className={
            state === "removing"
              ? styles.credentialWarning
              : styles.credentialNote
          }
        >
          {t(
            state === "removing"
              ? "Removed when you save"
              : state === "configured"
                ? "Saved"
                : "Not configured",
          )}
        </span>
        {state === "removing" ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => onRemovingChange?.(false)}
          >
            {t("Keep")}
          </Button>
        ) : (
          <>
            <Button type="button" variant="ghost" size="sm" onClick={open}>
              {t(state === "configured" ? "Replace" : "Add")}
            </Button>
            {state === "configured" && onRemovingChange && (
              <Button
                type="button"
                variant="ghost"
                size="sm"
                onClick={() => {
                  onDiscard();
                  onRemovingChange(true);
                }}
              >
                {t("Remove")}
              </Button>
            )}
          </>
        )}
      </div>
    </SettingsRow>
  );
}
