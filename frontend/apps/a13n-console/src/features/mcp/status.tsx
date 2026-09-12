import { useTranslation } from "react-i18next";
import { StateBadge } from "../../shared/feedback";
import type { Schema } from "../../shared/api";

export function MCPStatusBadge({
  connection,
}: {
  connection: Schema["MCPConnection"];
}) {
  const { t } = useTranslation();
  const label =
    connection.status === "pending"
      ? t(
          connection.credential_configured
            ? "Verification needed"
            : "Setup needed",
        )
      : connection.status === "action_required"
        ? t(
            connection.status_reason === "reauthorization_required"
              ? "Reauthorization needed"
              : "Configuration needed",
          )
        : undefined;
  return <StateBadge state={connection.status} label={label} />;
}
