import { Button, SettingsRow } from "a13n-ui";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ErrorNotice, StatePill } from "../../shared/feedback";
import type { Schema } from "../../shared/api";

export function ConnectionTest({
  action,
  description,
  dirty = false,
  compact = false,
}: {
  action: () => Promise<Schema["ModelConnectionTestResult"]>;
  description: string;
  dirty?: boolean;
  compact?: boolean;
}) {
  const { t } = useTranslation();
  const test = useMutation({
    mutationFn: action,
    gcTime: 0,
  });
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
  return (
    <div className={compact ? "grid gap-3" : undefined}>
      {compact ? (
        <div className="flex flex-wrap items-center gap-x-3 gap-y-2">
          {button}
          <span className="text-xs text-muted-foreground">{hint}</span>
        </div>
      ) : (
        <SettingsRow
          stackOnNarrow={false}
          label={t("Connection")}
          description={hint}
        >
          {button}
        </SettingsRow>
      )}
      {!dirty && test.data && (
        <div role="status" className="pb-4 text-sm">
          <div className="flex items-center gap-2">
            <StatePill state={test.data.success ? "succeeded" : "failed"} />
            <span className="text-xs text-muted-foreground">
              {test.data.elapsed_ms} ms
            </span>
          </div>
          <p className="mt-2 text-muted-foreground">{test.data.message}</p>
        </div>
      )}
      {!dirty && <ErrorNotice error={test.error} />}
    </div>
  );
}
