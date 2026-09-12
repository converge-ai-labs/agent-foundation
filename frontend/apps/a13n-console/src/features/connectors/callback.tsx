import { useCallback, useEffect, useRef, useState } from "react";
import { Button } from "a13n-ui";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useAuth, useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { ErrorNotice, Loading, Page } from "../../shared/feedback";
import {
  clearAuthorization,
  readAuthorization,
  takeCallbackSession,
} from "./authorization-context";

// Eagerly imported by App so the sensitive query is removed before authentication starts.
let callbackSession = takeCallbackSession();

export function ConnectorSetupCallback() {
  const auth = useAuth(),
    client = useClient(),
    { t } = useTranslation();
  const started = useRef(false);
  const [error, setError] = useState<unknown>(null);
  const [context] = useState(readAuthorization);
  const [confirm, setConfirm] = useState(false);
  const [pending, setPending] = useState(false);
  const complete = useCallback(
    async (session?: string) => {
      if (!context) return;
      setPending(true);
      try {
        const result = data(
          await client.http.POST("/api/v1/connector-setup/complete", {
            body: {
              attempt_id: context.attempt_id!,
              browser_nonce: context.browser_nonce,
              ...(session ? { session_uri: session } : {}),
            },
          }),
        );
        const target = new URL(result.return_path, window.location.origin);
        if (
          target.origin !== window.location.origin ||
          target.pathname !== context.return_path
        )
          throw new Error(t("Invalid authorization return path."));
        target.searchParams.set("connection", context.connection_id);
        clearAuthorization();
        window.location.replace(target.href);
      } catch (failure: unknown) {
        clearAuthorization();
        setError(failure);
      } finally {
        setPending(false);
      }
    },
    [client, context, t],
  );

  useEffect(() => {
    if (auth.isPending || started.current) return;
    started.current = true;
    const session = callbackSession;
    callbackSession = null;
    if (auth.anonymous || auth.error || !context) {
      clearAuthorization();
      setError(
        new Error(
          t(
            "Authorization context is missing or expired. Sign in and start authorization again in this tab.",
          ),
        ),
      );
      return;
    }
    if (context.completion_method === "browser_confirmation") {
      setConfirm(true);
      return;
    }
    if (!session) {
      clearAuthorization();
      setError(
        new Error(
          t(
            "OAuth verification was not returned. Configure the Composio project's OAuth user verification URL and restart authorization.",
          ),
        ),
      );
      return;
    }
    void complete(session);
  }, [auth.isPending, auth.anonymous, auth.error, complete, context, t]);

  const returnTo = context
    ? `${context.return_path}?connection=${encodeURIComponent(context.connection_id)}`
    : "/login";
  return (
    <Page title={t(confirm ? "Confirm connection" : "Verifying authorization")}>
      <div className="max-w-xl space-y-4">
        {error ? (
          <>
            <ErrorNotice error={error} />
            <p>
              {t(
                "Check the connection status before starting another authorization.",
              )}
            </p>
            <Link to={returnTo}>{t("Continue")}</Link>
          </>
        ) : confirm && context ? (
          <>
            <p>
              {t("Connect {{connection}} to {{workspace}}?", {
                connection: context.connection_name,
                workspace: context.workspace_name,
              })}
            </p>
            <p>
              {t(
                "Only confirm if you entered the account credentials yourself in this tab. Composio cannot verify which browser submitted API key, bearer token, or basic authentication credentials.",
              )}
            </p>
            <div className="flex items-center gap-4">
              <Button
                type="button"
                disabled={pending}
                onClick={() => void complete()}
              >
                {t(pending ? "Connecting…" : "Confirm connection")}
              </Button>
              <Link to={returnTo} onClick={clearAuthorization}>
                {t("Cancel")}
              </Link>
            </div>
          </>
        ) : (
          <Loading />
        )}
      </div>
    </Page>
  );
}
