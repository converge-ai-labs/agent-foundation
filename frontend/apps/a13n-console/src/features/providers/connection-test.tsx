import { useMutation } from "@tanstack/react-query";
import { Button, SettingsRow } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { ErrorNotice, StatePill } from "../../shared/feedback";
import styles from "./providers.module.css";

/** What a category learned by reaching the service, however it asked. */
export interface ConnectionTestResult {
  success: boolean;
  message: string;
  /** Reported where the category measures the round trip. */
  elapsed_ms?: number;
}

/**
 * Checking a saved connection: one button, the cost it may carry, and what it
 * found. The footer placement puts it beside the editor's own actions.
 */
export function ConnectionTest({
  action,
  description,
  dirty = false,
  placement = "row",
  retry,
}: {
  action: () => Promise<ConnectionTestResult>;
  description: string;
  /** An unsaved draft cannot be checked; the button says so instead. */
  dirty?: boolean;
  placement?: "row" | "footer";
  retry?: () => void;
}) {
  const { t } = useTranslation();
  const test = useMutation({ mutationFn: action, gcTime: 0 });
  const hint = t(
    dirty ? "Save your changes before checking the connection." : description,
  );
  const button = (
    <Button
      type="button"
      size="sm"
      variant="outline"
      disabled={dirty}
      loading={test.isPending}
      onClick={() => test.mutate()}
    >
      {t("Check connection")}
    </Button>
  );
  const result = !dirty && test.data && (
    <div role="status" className={styles.checkResult}>
      <span className={styles.checkRow}>
        <StatePill state={test.data.success ? "succeeded" : "failed"} />
        {test.data.elapsed_ms !== undefined && (
          <span className={styles.checkNote}>{test.data.elapsed_ms} ms</span>
        )}
      </span>
      <p className={styles.checkMessage}>{test.data.message}</p>
    </div>
  );
  if (placement === "footer")
    return (
      <>
        <span className={styles.checkRow}>
          {button}
          <span className={styles.checkNote}>{hint}</span>
        </span>
        {result}
        {!dirty && <ErrorNotice error={test.error} retry={retry} />}
      </>
    );
  return (
    <div>
      <SettingsRow
        stackOnNarrow={false}
        label={t("Connection")}
        description={hint}
      >
        {button}
      </SettingsRow>
      {result}
      {!dirty && <ErrorNotice error={test.error} retry={retry} />}
    </div>
  );
}
