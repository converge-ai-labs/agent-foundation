import { useEffect, useRef, useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useAuth, useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { ErrorNotice, Loading, Page } from "../../shared/feedback";
import {
  clearMCPAuthorization,
  readMCPAuthorization,
  takeMCPCallback,
} from "./authorization-context";

let callback = takeMCPCallback();
export function MCPSetupCallback() {
  const auth = useAuth(),
    client = useClient(),
    { t } = useTranslation();
  const started = useRef(false),
    [context] = useState(readMCPAuthorization),
    [error, setError] = useState<unknown>(null);
  useEffect(() => {
    if (auth.isPending || started.current) return;
    started.current = true;
    const response = callback;
    callback = null;
    if (
      auth.anonymous ||
      auth.error ||
      !context ||
      !response ||
      response.state !== context.state
    ) {
      clearMCPAuthorization();
      setError(
        new Error(
          t(
            "Authorization context is missing or expired. Sign in and start authorization again in this tab.",
          ),
        ),
      );
      return;
    }
    void client.http
      .POST("/api/v1/oauth/mcp/complete", { body: response })
      .then(data)
      .then((connection) => {
        if (
          connection.id !== context.connectionId ||
          connection.workspace_id !== context.workspaceId
        )
          throw new Error(t("Authorization returned a different connection."));
        const target = new URL(context.returnPath, window.location.origin);
        target.searchParams.set("connection", connection.id);
        clearMCPAuthorization();
        window.location.replace(target.href);
      })
      .catch((failure: unknown) => {
        clearMCPAuthorization();
        setError(failure);
      });
  }, [auth.isPending, auth.anonymous, auth.error, client, context, t]);
  return (
    <Page title={t("Verifying authorization")}>
      {error ? (
        <>
          <ErrorNotice error={error} />
          <p>
            {t(
              "Check the connection status before starting another authorization.",
            )}
          </p>
          <Link
            to={
              context
                ? `${context.returnPath}?connection=${encodeURIComponent(context.connectionId)}`
                : "/login"
            }
          >
            {t("Continue")}
          </Link>
        </>
      ) : (
        <Loading />
      )}
    </Page>
  );
}
