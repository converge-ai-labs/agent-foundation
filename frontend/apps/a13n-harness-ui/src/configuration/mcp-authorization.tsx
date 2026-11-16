import { useEffect, useRef, useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { ArrowSquareOut } from "@phosphor-icons/react";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { ConfirmAction } from "../shell/confirm-action";
import { ErrorNotice } from "../shell/ui";
import styles from "./mcp-authorization.module.css";

/** One authorization interaction for configured MCP resources and concrete Connectors. */
export function McpAuthorization({
  target,
  onConnected,
}: {
  target: Schema<"AuthorizationTarget">;
  onConnected?: () => void;
}) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const cache = useQueryClient();
  const key = ["mcp-authorization", target.kind, target.id];
  const [uncertain, setUncertain] = useState(false);
  const observed = useRef(false);
  const status = useQuery({
    queryKey: key,
    retry: false,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/mcp-authorization/status/{kind}/{target_id}", {
          params: { path: { kind: target.kind, target_id: target.id } },
          signal,
        }),
      ),
    refetchInterval: (query) =>
      ["connecting", "waiting"].includes(query.state.data?.login?.state ?? "")
        ? 1000
        : false,
  });
  const connected = status.data?.state === "connected";
  const login = status.data?.login;
  const pending = login?.state === "waiting" || login?.state === "connecting";
  useEffect(() => {
    if (connected && !observed.current) onConnected?.();
    observed.current = connected;
  }, [connected, onConnected]);
  async function refresh() {
    await status.refetch();
    await cache.invalidateQueries({ queryKey: ["connectors"] });
  }
  const start = useMutation({
    retry: false,
    mutationFn: () =>
      result(client.POST("/api/mcp-authorization/start", { body: target })),
    onSuccess: async () => {
      setUncertain(false);
      await refresh();
    },
    onError: () => setUncertain(true),
  });
  const cancel = useMutation({
    mutationFn: () =>
      result(
        client.DELETE("/api/mcp-authorization/attempts/{attempt_id}", {
          params: { path: { attempt_id: login!.attempt_id } },
        }),
      ),
    onSuccess: refresh,
  });
  const disconnect = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/mcp-authorization/disconnect", { body: target }),
      ),
    onSuccess: refresh,
  });
  const busy = start.isPending || cancel.isPending || disconnect.isPending;
  function connect() {
    // Reserve the browser tab during the gesture; discovery itself is an explicit Host operation.
    const tab = window.open("", "_blank");
    if (tab) tab.opener = null;
    start.mutate(undefined, {
      onSuccess: (attempt) => {
        if (tab && attempt.authorization_url)
          tab.location.href = attempt.authorization_url;
        else tab?.close();
      },
      onError: () => tab?.close(),
    });
  }
  return (
    <div className={styles.authorization}>
      <p role="status" className={styles.note}>
        {pending
          ? t("Waiting for browser authorization…")
          : connected
            ? t("Connected")
            : status.data?.state === "reconnect_required" ||
                status.data?.state === "expired"
              ? t(
                  "Authorization expired or needs attention. Reconnect to continue.",
                )
              : t(
                  "Authorize access in your browser. Tokens stay in this Harness UI instance.",
                )}
      </p>
      {!!status.data?.scopes?.length && (
        <p className={styles.note}>
          {t("Granted permissions")}: {status.data.scopes.join(", ")}
        </p>
      )}
      <ErrorNotice
        error={status.error ?? start.error ?? cancel.error ?? disconnect.error}
      />
      {login?.state === "failed" && login.error && (
        <p role="alert" className={styles.note}>
          {t(login.error)}
        </p>
      )}
      {uncertain && (
        <p className={styles.note}>
          {t(
            "The login request outcome is unknown. Refresh status before starting again.",
          )}
        </p>
      )}
      <div className={styles.actions}>
        {pending && login.authorization_url && (
          <Button
            render={
              <a
                href={login.authorization_url}
                target="_blank"
                rel="noreferrer"
              />
            }
          >
            <ArrowSquareOut aria-hidden="true" />
            {t("Continue authorization")}
          </Button>
        )}
        {pending && (
          <Button
            variant="outline"
            loading={cancel.isPending}
            disabled={busy}
            onClick={() => cancel.mutate()}
          >
            {t("Cancel authorization")}
          </Button>
        )}
        {!pending && !uncertain && (
          <Button
            variant={connected ? "outline" : "default"}
            disabled={busy || status.isPending || !!status.error}
            loading={start.isPending}
            onClick={connect}
          >
            {connected ? t("Reconnect") : t("Connect")}
          </Button>
        )}
        {(uncertain || !!status.error) && (
          <Button
            variant="outline"
            disabled={busy}
            onClick={async () => {
              await refresh();
              start.reset();
              setUncertain(false);
            }}
          >
            {t("Refresh status")}
          </Button>
        )}
        {connected && !pending && (
          <ConfirmAction
            trigger={
              <Button variant="ghost" disabled={busy}>
                {t("Disconnect")}
              </Button>
            }
            title={t("Disconnect this account?")}
            description={t(
              "New requests will stop using this authorization. Existing chats must start a new run after reconnecting.",
            )}
            confirmLabel={t("Disconnect")}
            destructive
            onConfirm={() => disconnect.mutate()}
          />
        )}
      </div>
    </div>
  );
}
