import { useEffect, useLayoutEffect, useState, type ReactNode } from "react";
import { Button, Spinner } from "a13n-ui";
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
  const [slow, setSlow] = useState(false);
  useEffect(() => {
    if (ready) return;
    const timer = setTimeout(() => setSlow(true), 6000);
    return () => clearTimeout(timer);
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
      {!ready && (
        <div className={styles.loading}>
          <div role="status" className={styles.status}>
            <Spinner aria-hidden="true" />
            <span>{label}</span>
          </div>
          {slow && (
            <div className={styles.recovery}>
              <p>Taking longer than usual. Your input is still here.</p>
              <Button variant="ghost" size="sm" onClick={onContinue}>
                Show available conversation
              </Button>
            </div>
          )}
        </div>
      )}
    </div>
  );
}
