import { StatusPill } from "a13n-ui";
import { useTranslation } from "../i18n";
import type { Schema } from "../transport/client";

export function ConnectorStatus({
  instance,
}: {
  instance: Schema<"Instance">;
}) {
  const { t } = useTranslation();
  const state = instance.status.state;
  const label =
    state === "ready"
      ? "Ready"
      : state === "unconfigured"
        ? "Setup incomplete"
        : state === "disabled"
          ? "Disabled"
          : "Unavailable";
  return (
    <StatusPill
      variant={
        state === "ready"
          ? "success"
          : state === "unconfigured"
            ? "warning"
            : "neutral"
      }
    >
      {t(label)}
    </StatusPill>
  );
}
