import { useEffect, useRef, useState } from "react";
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
  useEffect(() => {
    if (auth.isPending || started.current) return;
    started.current = true;
    const session = callbackSession;
    callbackSession = null;
    if (auth.anonymous || auth.error || !context || !session) {
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
    void client.http
      .POST("/api/v1/connector-setup/complete", {
        body: {
          attempt_id: context.attempt_id!,
          browser_nonce: context.browser_nonce,
          session_uri: session,
        },
      })
      .then(data)
      .then((result) => {
        const target = new URL(result.return_path, window.location.origin);
        if (
          target.origin !== window.location.origin ||
          target.pathname !== context.return_path
        )
          throw new Error(t("Invalid authorization return path."));
        target.searchParams.set("connection", context.connection_id);
        clearAuthorization();
        window.location.replace(target.href);
      })
      .catch((failure: unknown) => {
        clearAuthorization();
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
                ? `${context.return_path}?connection=${encodeURIComponent(context.connection_id)}`
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
