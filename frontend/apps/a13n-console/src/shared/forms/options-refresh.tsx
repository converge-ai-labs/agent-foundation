import { ArrowClockwiseIcon } from "@phosphor-icons/react";
import { Button } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { ErrorToast } from "../feedback";

/** Refresh a picker's options without changing its search or selected value. */
export function OptionsRefresh({
  onRefresh,
}: {
  onRefresh: () => Promise<unknown>;
}) {
  const { t } = useTranslation();
  const [pending, setPending] = useState(false);
  const [error, setError] = useState<Error>();
  async function refresh() {
    if (pending) return;
    setPending(true);
    setError(undefined);
    try {
      await onRefresh();
    } catch (failure) {
      setError(failure instanceof Error ? failure : new Error(String(failure)));
    } finally {
      setPending(false);
    }
  }
  return (
    <>
      <Button
        type="button"
        variant="ghost"
        size="icon-xs"
        className="size-6 text-muted-foreground"
        aria-label={t("Refresh")}
        title={t("Refresh")}
        loading={pending}
        disabled={pending}
        onClick={() => void refresh()}
      >
        <ArrowClockwiseIcon className="size-3.5" aria-hidden="true" />
      </Button>
      <ErrorToast error={error} />
    </>
  );
}
