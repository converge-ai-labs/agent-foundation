import { useTranslation } from "react-i18next";
import type { Schema } from "../../shared/api";
import { StatePill } from "../../shared/feedback";

/** Lifecycle readiness alone does not make a permanently failed target usable. */
export function EnvironmentStatus({
  environment,
}: {
  environment: Pick<Schema["EnvironmentView"], "status" | "failure">;
}) {
  const { t } = useTranslation();
  if (environment.failure?.code === "environment_lost")
    return <StatePill state="failed" label={t("Environment lost")} />;
  if (environment.status === "creating" && environment.failure?.permanent)
    return <StatePill state="failed" label={t("Creation failed")} />;
  if (environment.status === "ready" && environment.failure?.permanent)
    return <StatePill state="failed" label={t("Environment unavailable")} />;
  return <StatePill state={environment.status} />;
}
