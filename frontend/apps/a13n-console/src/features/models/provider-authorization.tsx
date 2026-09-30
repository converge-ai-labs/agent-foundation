import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { type Schema } from "../../shared/api";
import { AuthorizationLink } from "../../shared/authorization-link";
import { ErrorNotice } from "../../shared/feedback";
import { modelApi } from "./api";

/** Provider-wide authorization; callback material lives only in this mounted editor. */
export function ProviderAuthorization({
  provider,
}: {
  provider: Schema["Provider"];
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    { can } = useWorkspace(),
    api = modelApi(client, provider.workspace_id),
    [attempt, setAttempt] = useState<Schema["AuthorizationStart"]>(),
    [callback, setCallback] = useState(""),
    [revocationUnconfirmed, setRevocationUnconfirmed] = useState(false);
  const queryKey = [
    "model-provider-authorization",
    provider.workspace_id,
    provider.id,
  ];
  const status = useQuery({
    queryKey,
    queryFn: ({ signal }) => api.authorization(provider.id, signal),
  });
  const refresh = () => cache.invalidateQueries({ queryKey });
  const start = useMutation({
    gcTime: 0,
    mutationFn: (newRegistration: boolean) =>
      api.authorize(provider.id, newRegistration),
    onMutate: () => {
      setCallback("");
      setAttempt(undefined);
      setRevocationUnconfirmed(false);
    },
    onSuccess: (value) => {
      setAttempt(value);
      void refresh();
    },
  });
  const complete = useMutation({
    gcTime: 0,
    mutationFn: () => {
      if (!attempt) throw new Error(t("Start authorization first."));
      const callback_url = callback;
      // Clear before dispatch: mutation variables and cache never retain the pasted secret.
      setCallback("");
      return api.completeAuthorization(provider.id, {
        attempt_id: attempt.attempt_id,
        callback_url,
      });
    },
    onSuccess: () => {
      setAttempt(undefined);
      start.reset();
      void refresh();
    },
    onError: () => {
      void refresh();
    },
  });
  const disconnect = useMutation({
    gcTime: 0,
    mutationFn: () => api.disconnect(provider.id),
    onMutate: () => {
      setCallback("");
      setAttempt(undefined);
      start.reset();
      complete.reset();
    },
    onSuccess: (value) => {
      setRevocationUnconfirmed(value.revocation_confirmed === false);
      void refresh();
    },
  });
  const writable = can("write"),
    busy = start.isPending || complete.isPending || disconnect.isPending,
    connected =
      status.data?.state === "connected" || status.data?.state === "refreshing";
  return (
    <section className="grid gap-3" aria-label={t("ChatGPT authorization")}>
      <div>
        <strong>{t("ChatGPT authorization")}</strong>
        <p className="text-sm text-muted-foreground">
          {t("Shared by this workspace, not a personal connection.")}
        </p>
      </div>
      <p role="status">
        {status.data?.email ?? t(connected ? "Connected" : "Not connected")}
      </p>
      {status.data?.message && <p className="text-sm">{status.data.message}</p>}
      {attempt && (
        <>
          <AuthorizationLink
            url={attempt.authorization_url}
            expiresAt={attempt.expires_at}
          />
          <FormField
            label={t("Complete callback URL")}
            description={t(
              "After signing in, copy the entire URL from the browser address bar, even if the loopback page cannot be reached.",
            )}
          >
            <Input
              type="password"
              autoComplete="off"
              spellCheck={false}
              maxLength={16384}
              value={callback}
              onChange={(event) => setCallback(event.target.value)}
            />
          </FormField>
          <Button
            type="button"
            variant="outline"
            disabled={!callback.trim() || busy || !writable}
            loading={complete.isPending}
            onClick={() => complete.mutate()}
          >
            {t("Complete sign-in")}
          </Button>
        </>
      )}
      <div className="flex flex-wrap gap-2">
        <Button
          type="button"
          variant="outline"
          disabled={busy || !writable}
          loading={start.isPending}
          onClick={() => {
            complete.reset();
            start.mutate(false);
          }}
        >
          {t(
            connected || attempt
              ? "Restart authorization"
              : "Sign in with ChatGPT",
          )}
        </Button>
        {status.data?.client_id && (
          <Button
            type="button"
            variant="ghost"
            disabled={busy || !writable}
            onClick={() => {
              complete.reset();
              start.mutate(true);
            }}
          >
            {t("Use another ChatGPT account")}
          </Button>
        )}
        {(connected ||
          attempt ||
          status.data?.state === "reauthentication_required") && (
          <Button
            type="button"
            variant="ghost"
            disabled={busy || !writable}
            loading={disconnect.isPending}
            onClick={() => disconnect.mutate()}
          >
            {t("Disconnect")}
          </Button>
        )}
      </div>
      {revocationUnconfirmed && (
        <p role="status">
          {t("Local tokens were cleared. OpenAI revocation was not confirmed.")}
        </p>
      )}
      <ErrorNotice
        error={
          status.error ?? start.error ?? complete.error ?? disconnect.error
        }
      />
    </section>
  );
}
