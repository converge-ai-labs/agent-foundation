import { Button, SettingsRow } from "a13n-ui";
import { useMutation } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { ErrorNotice, StateBadge } from "../../shared/feedback";
import type { Schema } from "../../shared/api";

export function ConnectionTest({
  action,
  description,
  dirty = false,
}: {
  action: () => Promise<Schema["ModelConnectionTestResult"]>;
  description: string;
  dirty?: boolean;
}) {
  const { t } = useTranslation();
  const test = useMutation({
    mutationFn: action,
    gcTime: 0,
  });
  return (
    <div>
      <SettingsRow
        stackOnNarrow={false}
        label={t("Connection")}
        description={t(
          dirty
            ? "Save your changes before checking the connection."
            : description,
        )}
      >
        <Button
          size="sm"
          variant="outline"
          disabled={dirty}
          loading={test.isPending}
          onClick={() => test.mutate()}
        >
          {t("Check connection")}
        </Button>
      </SettingsRow>
      {!dirty && test.data && (
        <div role="status" className="pb-4 text-sm">
          <div className="flex items-center gap-2">
            <StateBadge state={test.data.success ? "succeeded" : "failed"} />
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
