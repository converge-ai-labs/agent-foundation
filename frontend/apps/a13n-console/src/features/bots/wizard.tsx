import { CheckIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import type { ReactNode } from "react";
import { useTranslation } from "react-i18next";
import styles from "./connect.module.css";

/** The quiet progress column beside the wizard content. */
export function StepRail({
  steps,
  current,
  label,
}: {
  steps: readonly string[];
  current: number;
  label: string;
}) {
  const { t } = useTranslation();
  return (
    <ol className={styles.steps} aria-label={label}>
      {steps.map((step, index) => {
        const state =
          index < current ? "done" : index === current ? "current" : "todo";
        return (
          <li
            key={step}
            data-state={state}
            aria-current={state === "current" ? "step" : undefined}
          >
            <span className={styles.stepMark} aria-hidden="true">
              {state === "done" ? (
                <CheckIcon size={12} weight="bold" />
              ) : (
                index + 1
              )}
            </span>
            <span>{t(step)}</span>
          </li>
        );
      })}
    </ol>
  );
}

/**
 * One wizard step: a titled panel whose actions sit at the bottom, back at the
 * left and the single primary action at the right.
 */
export function Step({
  title,
  description,
  onBack,
  backLabel,
  backDisabled,
  primary,
  children,
}: {
  title: string;
  description?: string;
  onBack?: () => void;
  backLabel?: string;
  backDisabled?: boolean;
  primary?: ReactNode;
  children: ReactNode;
}) {
  const { t } = useTranslation();
  return (
    <section className={styles.step}>
      <header className={styles.stepHeader}>
        <h2>{title}</h2>
        {description && <p>{description}</p>}
      </header>
      <div className={styles.stepBody}>{children}</div>
      {(onBack || primary) && (
        <footer className={styles.stepFooter}>
          {onBack ? (
            <Button
              type="button"
              variant="ghost"
              disabled={backDisabled}
              onClick={onBack}
            >
              {backLabel ?? t("Back")}
            </Button>
          ) : (
            <span />
          )}
          <div className={styles.stepPrimary}>{primary}</div>
        </footer>
      )}
    </section>
  );
}
