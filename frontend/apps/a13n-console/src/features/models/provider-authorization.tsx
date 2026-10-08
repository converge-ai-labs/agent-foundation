import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, FormField, Input } from "a13n-ui";
import { useEffect, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { type Schema } from "../../shared/api";
import { AuthorizationLink } from "../../shared/authorization-link";
import { ErrorNotice } from "../../shared/feedback";
import { discoveryKey, modelApi } from "./api";

/** Provider-wide authorization; callback material lives only in this mounted editor. */
export function ProviderAuthorization({
  provider,
  configurationDirty = false,
}: {
  provider: Schema["Provider"];
  configurationDirty?: boolean;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient(),
    { can } = useWorkspace(),
    api = modelApi(client, provider.workspace_id),
    [attempt, setAttempt] = useState<Schema["AuthorizationStart"]>(),
    [attemptStartedAt, setAttemptStartedAt] = useState(0),
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
    refetchInterval:
      attempt?.method === "browser_callback" &&
      Date.parse(attempt.expires_at) > Date.now()
        ? 1500
        : false,
  });
  useEffect(() => {
    if (
      attempt?.method === "browser_callback" &&
      status.dataUpdatedAt > attemptStartedAt &&
      !status.data?.pending &&
      status.data?.state === "connected"
    ) {
      setAttempt(undefined);
      setCallback("");
      void cache.invalidateQueries({
        queryKey: discoveryKey(provider.workspace_id, provider.id),
      });
    }
  }, [
    attempt,
    attemptStartedAt,
    status.data,
    status.dataUpdatedAt,
    cache,
    provider.workspace_id,
    provider.id,
  ]);
  const refresh = () => cache.invalidateQueries({ queryKey });
  const clearModels = async () => {
    const key = discoveryKey(provider.workspace_id, provider.id);
    await cache.cancelQueries({ queryKey: key });
    cache.setQueryData(key, []);
    await cache.invalidateQueries({ queryKey: key, refetchType: "none" });
  };
  const pauseDiscovery = async () => {
    await cache.cancelQueries({ queryKey });
    cache.setQueryData<Schema["AuthorizationStatus"]>(queryKey, (current) =>
      current ? { ...current, pending: true } : current,
    );
    await clearModels();
  };
  const start = useMutation({
    gcTime: 0,
    mutationFn: (newRegistration: boolean) =>
      api.authorize(provider.id, newRegistration),
    onMutate: async () => {
      setCallback("");
      setAttempt(undefined);
      setRevocationUnconfirmed(false);
      await pauseDiscovery();
    },
    onError: () => {
      void refresh();
    },
    onSuccess: (value) => {
      setAttemptStartedAt(Date.now());
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
    onSuccess: async (value) => {
      await clearModels();
      setAttempt(undefined);
      start.reset();
      cache.setQueryData(queryKey, value);
      void refresh();
      void cache.invalidateQueries({
        queryKey: discoveryKey(provider.workspace_id, provider.id),
      });
    },
    onError: () => {
      void refresh();
    },
  });
  const disconnect = useMutation({
    gcTime: 0,
    mutationFn: () => api.disconnect(provider.id),
    onMutate: async () => {
      setCallback("");
      setAttempt(undefined);
      start.reset();
      complete.reset();
      await pauseDiscovery();
    },
    onError: () => {
      void refresh();
    },
    onSuccess: (value) => {
      cache.setQueryData<Schema["AuthorizationStatus"]>(queryKey, (current) =>
        current
          ? { ...current, state: "disconnected", pending: false }
          : current,
      );
      setRevocationUnconfirmed(value.revocation_confirmed === false);
      void refresh();
    },
  });
  const writable = can("write") && !configurationDirty,
    busy = start.isPending || complete.isPending || disconnect.isPending,
    connected =
      status.data?.state === "connected" || status.data?.state === "refreshing";
  return (
    <section
      className="grid min-w-0 gap-4"
      aria-label={t("ChatGPT authorization")}
    >
      <div>
        <strong>{t("ChatGPT authorization")}</strong>
        <p className="text-sm text-muted-foreground">
          {t("Shared by this workspace, not a personal connection.")}
        </p>
      </div>
      {configurationDirty && (
        <p className="text-sm text-muted-foreground">
          {t(
            "Save configuration changes before starting or completing sign-in.",
          )}
        </p>
      )}
      <p role="status">
        {attempt
          ? t("Waiting for sign-in")
          : status.isPending
            ? t("Checking authorization…")
            : (status.data?.email ??
              t(connected ? "Connected" : "Not connected"))}
      </p>
      {status.data?.message && <p className="text-sm">{status.data.message}</p>}
      {attempt && (
        <>
          <AuthorizationLink
            url={attempt.authorization_url}
            expiresAt={attempt.expires_at}
          />
          {attempt.method === "browser_callback" && (
            <p className="text-sm text-muted-foreground">
              {t(
                "Sign-in completes automatically. Return here after authorizing in the browser.",
              )}
            </p>
          )}
          <FormField
            label={t("Complete callback URL")}
            description={t(
              attempt.method === "browser_callback"
                ? "If automatic completion fails, paste the entire callback URL here."
                : "After signing in, copy the entire URL from the browser address bar, even if the loopback page cannot be reached.",
            )}
          >
            <Input
              type="password"
              autoComplete="off"
              spellCheck={false}
              maxLength={16384}
              value={callback}
              disabled={busy || !writable}
              onChange={(event) => setCallback(event.target.value)}
              onKeyDown={(event) => {
                if (event.key !== "Enter") return;
                event.preventDefault();
                if (callback.trim() && !busy && writable) complete.mutate();
              }}
            />
          </FormField>
        </>
      )}
      <div className="flex flex-wrap items-center gap-2">
        {attempt && (
          <Button
            type="button"
            variant="outline"
            disabled={!callback.trim() || busy || !writable}
            loading={complete.isPending}
            onClick={() => complete.mutate()}
          >
            {t("Complete sign-in")}
          </Button>
        )}
        <Button
          type="button"
          variant={attempt ? "ghost" : "outline"}
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
              : "Continue with ChatGPT",
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
            {t(attempt && !connected ? "Cancel authorization" : "Disconnect")}
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
