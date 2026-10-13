import { useMutation } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";

/** Explicit live probe; no screenshot, input, or implicit environment startup. */
export function ComputerCheck({ environmentId }: { environmentId: string }) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const check = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/environments/{environment_id}/computer/check", {
          params: { path: { environment_id: environmentId } },
        })
        .then(data),
  });
  const result = check.data;
  const label =
    result?.status === "unsupported"
      ? "This environment does not provide desktop operations."
      : result?.status === "not_ready"
        ? "The environment is not running. Start it before checking the desktop."
        : result?.observe_ready && result.input_ready
          ? "Desktop ready for observation and input."
          : result?.observe_ready
            ? "The screen is available, but desktop input is not ready."
            : "The desktop is not ready. Check its display and system permissions.";
  return (
    <div className="flex flex-col items-start gap-2">
      <Button
        type="button"
        variant="outline"
        size="sm"
        disabled={check.isPending}
        onClick={() => check.mutate()}
      >
        {t(check.isPending ? "Checking desktop…" : "Check desktop")}
      </Button>
      {result && !check.isPending && !check.isError && (
        <div role="status" className="text-sm text-muted-foreground">
          <p>{t(label)}</p>
          {result.observe_ready && (
            <p>
              {t("{{count}} displays detected", {
                count: result.display_count,
              })}
            </p>
          )}
          {result.input_ready &&
            !result.operations?.includes("environment.computer.type_text") && (
              <p>
                {t(
                  "Literal text entry is not supported; supported key presses remain available.",
                )}
              </p>
            )}
          <p>
            {t(
              "This checks the environment only. Agent tools and mount permissions still apply at execution time.",
            )}
          </p>
        </div>
      )}
      <ErrorNotice error={check.error} />
    </div>
  );
}
