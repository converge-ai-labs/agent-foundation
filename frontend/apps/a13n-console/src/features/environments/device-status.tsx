import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { InlineLoading, StatePill } from "../../shared/feedback";

/** Connection observations, never the independently persisted lifecycle status. */
export function DeviceConnectionStatus({
  environment,
}: {
  environment: Schema["Environment"];
}) {
  const client = useClient(),
    { t } = useTranslation();
  const revoked = environment.device_registration === "revoked";
  const connection = useQuery({
    queryKey: ["environment-connection", environment.id],
    enabled: !revoked,
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/environments/{environment_id}/connection", {
          params: { path: { environment_id: environment.id } },
          signal,
        })
        .then(data),
    refetchInterval: 5000,
  });
  if (revoked) return <StatePill state="revoked" />;
  if (connection.error)
    return (
      <span className="text-xs text-muted-foreground">
        {t("Connection unavailable")}
      </span>
    );
  return connection.data ? (
    <StatePill state={connection.data.status} />
  ) : (
    <InlineLoading width="4rem" />
  );
}
