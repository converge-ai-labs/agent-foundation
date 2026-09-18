import { StatusPill } from "a13n-ui";
import { useTranslation } from "react-i18next";

/** What the console knows about a provider's stored secret. */
export type CredentialState = "configured" | "not_configured" | "not_required";

const variants = {
  configured: "success",
  not_configured: "warning",
  not_required: "neutral",
} as const;
const labels = {
  configured: "Configured",
  not_configured: "Not configured",
  not_required: "Not required",
} as const;

/** One expression of credential state for every provider category. */
export function CredentialsPill({ state }: { state: CredentialState }) {
  const { t } = useTranslation();
  return (
    <StatusPill variant={variants[state]} data-state={state}>
      {t(labels[state])}
    </StatusPill>
  );
}
