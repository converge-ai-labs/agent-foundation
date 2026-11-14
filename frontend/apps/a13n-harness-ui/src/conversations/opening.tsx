import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { Button } from "a13n-ui";
import { useTranslation } from "../i18n";
import styles from "./opening.module.css";

// Only the first observation gates presentation. Refreshes must not hide the
// transcript, recreate the editor, or take focus away from an authored draft.
export function useInitialReady(ready: boolean) {
  const [revealed, setRevealed] = useState(false);
  useLayoutEffect(() => {
    if (ready) setRevealed(true);
  }, [ready]);
  return revealed || ready;
}

export function ConversationOpening({
  ready,
  label,
  onContinue,
  children,
}: {
  ready: boolean;
  label: string;
  onContinue: () => void;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (ready) return;
    const indicator = setTimeout(() => setPending(true), 350);
    const recovery = setTimeout(() => setSlow(true), 6000);
    return () => {
      clearTimeout(indicator);
      clearTimeout(recovery);
    };
  }, [ready]);
  return (
    <div className={styles.surface}>
      <div
        className={styles.content}
        data-ready={ready}
        inert={!ready}
        aria-hidden={!ready || undefined}
      >
        {children}
      </div>
      {!ready && pending && (
        <div className={styles.loading}>
          <div role="status" className={styles.status}>
            <span>{label}</span>
          </div>
          {slow && (
            <div className={styles.recovery}>
              <p>{t("Taking longer than usual. Your input is still here.")}</p>
              <Button variant="ghost" size="sm" onClick={onContinue}>
                {t("Open chat")}
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
