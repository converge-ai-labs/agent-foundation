import { useEffect, useRef, useState } from "react";
import { Button } from "a13n-ui";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useAuth, useClient } from "../../auth/context";
import { data } from "../../shared/api";
import { ErrorNotice, Loading, Page } from "../../shared/feedback";
import {
  clearAuthorization,
  readAuthorization,
  takeCallback,
  requireCompletedAuthorization,
} from "./authorization-context";

let callback = takeCallback();
export function ConnectionAuthorizationCallback() {
  const auth = useAuth(),
    client = useClient(),
    { t } = useTranslation();
  const started = useRef(false),
    [context] = useState(readAuthorization),
    [error, setError] = useState<unknown>(null),
    [confirmed, setConfirmed] = useState(false);
  useEffect(() => {
    if (
      auth.isPending ||
      started.current ||
      (callback?.type === "connector" && !confirmed)
    )
      return;
    started.current = true;
    const response = callback;
    callback = null;
    if (
      auth.anonymous ||
      auth.error ||
      !context ||
      !response ||
      response.state !== context.state ||
      response.type !== context.type ||
      (response.type === "connector" &&
        response.authorizationId !== context.authorizationId)
    ) {
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
      .POST("/api/v1/connection-authorizations/{authorization_id}/complete", {
        params: { path: { authorization_id: context.authorizationId } },
        body:
          response.type === "connector"
            ? {
                receipt: response.receipt,
                completion_verifier:
                  context.type === "connector" ? context.verifier : undefined,
              }
            : {
                state: response.state,
                ...(response.code ? { code: response.code } : {}),
                ...(response.iss ? { iss: response.iss } : {}),
                ...(response.error ? { error: response.error } : {}),
              },
      })
      .then(data)
      .then(requireCompletedAuthorization)
      .then((connection) => {
        if (
          connection.connection_id !== context.connectionId ||
          connection.id !== context.authorizationId
        )
          throw new Error(t("Authorization returned a different connection."));
        const target = new URL(context.returnPath, window.location.origin);
        target.searchParams.set("connection", connection.connection_id);
        clearAuthorization();
        window.location.replace(target.href);
      })
      .catch((failure: unknown) => {
        clearAuthorization();
        setError(failure);
      });
  }, [
    auth.isPending,
    auth.anonymous,
    auth.error,
    client,
    context,
    t,
    confirmed,
  ]);
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
      ) : callback?.type === "connector" && !confirmed && !auth.isPending ? (
        <div className="grid justify-items-start gap-4">
          <p>
            {t(
              "Confirm that you entered the credentials for this connection. Do not complete a link filled out by someone else.",
            )}
          </p>
          <p>
            {context?.connectionId} · {context?.workspaceId}
          </p>
          <Button type="button" onClick={() => setConfirmed(true)}>
            {t("Complete authorization")}
          </Button>
        </div>
      ) : (
        <Loading />
      )}
    </Page>
  );
}
