import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useEffect, useRef, useState } from "react";
import { Link, useNavigate, useSearchParams } from "react-router";
import { ErrorNotice } from "../shell/ui";
import {
  SettingsChoice,
  SettingsRow,
  SettingsSection,
} from "../shell/settings";
import { useTranslation } from "../i18n";
import { useTransport } from "../transport/context";
import { result, type Schema } from "../transport/client";
import { McpInput } from "../conversations/mcp-inputs";

type AcpAuthTarget =
  | { agentId: string; candidate?: never }
  | {
      agentId?: never;
      candidate: Schema<"AcpAgentCandidate">;
      detected: boolean;
      installUrl?: string | null;
      onReady: (ready: boolean, needsSignIn: boolean) => void;
    };

export function AcpAuth(target: AcpAuthTarget) {
  const { agentId, candidate } = target;
  const { t } = useTranslation();
  const { client } = useTransport();
  const navigate = useNavigate();
  const [search] = useSearchParams();
  const returnThread = search.get("returnThread");
  const [methodId, setMethod] = useState("");
  const [reauth, setReauth] = useState(false);
  const params = { path: { agent_id: agentId ?? "" } };
  const loginRequest = useRef<AbortController>(null);
  useEffect(() => () => loginRequest.current?.abort(), []);
  const request = (body: Schema<"AcpAuthRequest">, signal?: AbortSignal) =>
    candidate
      ? result(
          client.POST("/api/acp-agents/preset-auth", {
            body: { ...candidate, ...body },
            signal,
          }),
        )
      : result(
          client.POST("/api/acp-agents/{agent_id}/auth", {
            params,
            body,
            signal,
          }),
        );
  const probe = useQuery({
    queryKey: ["acp-auth", agentId ?? candidate],
    queryFn: ({ signal }) => request({ action: "probe" }, signal),
    enabled: !target.candidate || target.detected,
    refetchOnMount: "always",
    refetchOnWindowFocus: false,
    retry: false,
  });
  const auth = useMutation({
    mutationFn: (body: Schema<"AcpAuthRequest">) => {
      loginRequest.current = new AbortController();
      return request(body, loginRequest.current.signal);
    },
    onSuccess: async () => {
      setReauth(false);
      await probe.refetch();
    },
  });
  const start = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/threads", {
          body: {
            defaults: {
              agent_id: agentId!,
              environment_profile_id: "environment-native",
              environment_bindings: [],
            },
          },
        }),
      ),
    onSuccess: (thread) =>
      navigate(`/threads/${encodeURIComponent(thread.thread_id)}?compose=1`),
  });
  const status = probe.data;
  const ready = status?.state === "ready";
  const canContinue =
    !auth.isPending &&
    !probe.isFetching &&
    (ready || status?.state === "auth_required");
  const onReady = target.candidate ? target.onReady : undefined;
  useEffect(() => {
    onReady?.(canContinue, canContinue && status?.state === "auth_required");
  }, [canContinue, onReady, status?.state]);
  const methods = status?.methods ?? [];
  const method = methods.find(({ id }) => id === methodId) ?? methods[0];
  const signingIn = auth.isPending && auth.variables?.action === "login";
  const inputs = useQuery({
    queryKey: ["acp-auth-inputs", agentId ?? candidate],
    enabled: signingIn,
    refetchInterval: signingIn ? 800 : false,
    queryFn: ({ signal }) =>
      candidate
        ? result(client.GET("/api/acp-agents/preset-auth/inputs", { signal }))
        : result(
            client.GET("/api/acp-agents/{agent_id}/auth/inputs", {
              params,
              signal,
            }),
          ),
  });
  const terminalCommand =
    method?.argv
      ?.map((argument) => `'${argument.replaceAll("'", "'\\''")}'`)
      .join(" ") ?? "";
  const missing =
    status?.state === "executable_missing" ||
    !!(target.candidate && !target.detected && !status && target.installUrl);
  const uncheckedCustom =
    target.candidate && !target.detected && !status && !target.installUrl;
  const message = probe.isFetching
    ? t("Checking whether the Agent is ready…")
    : missing
      ? t("The configured executable was not found.")
      : status?.state === "auth_required"
        ? t("Sign in to use this Agent.")
        : uncheckedCustom
          ? t("Check the executable before continuing.")
          : undefined;
  const content = (
    <>
      <SettingsRow
        label={ready ? t("Ready · Signed in") : t("ACP connection")}
        description={
          ready ? (
            [status.agent_name, status.agent_version, status.default_model]
              .filter(Boolean)
              .join(" · ")
          ) : (
            <>
              {message}
              {missing && target.candidate && target.installUrl && (
                <>
                  {" "}
                  <a href={target.installUrl} target="_blank" rel="noreferrer">
                    {t("Installation instructions")}
                  </a>
                </>
              )}
            </>
          )
        }
      >
        {ready && !candidate ? (
          returnThread ? (
            <Button
              render={
                <Link to={`/threads/${encodeURIComponent(returnThread)}`} />
              }
            >
              {t("Back to chat")}
            </Button>
          ) : (
            <Button
              disabled={start.isPending || auth.isPending}
              onClick={() => start.mutate()}
            >
              {t("Start chat")}
            </Button>
          )
        ) : (
          <Button
            variant="outline"
            disabled={auth.isPending || probe.isFetching}
            onClick={() => void probe.refetch()}
          >
            {uncheckedCustom ? t("Check connection") : t("Check again")}
          </Button>
        )}
      </SettingsRow>
      {!candidate &&
        ready &&
        (methods.length > 0 || status.logout_supported) && (
          <SettingsRow label={t("Agent account")}>
            {methods.length > 0 && (
              <Button
                variant="ghost"
                disabled={auth.isPending}
                onClick={() => setReauth(!reauth)}
              >
                {t("Re-authenticate")}
              </Button>
            )}
            {status.logout_supported && (
              <Button
                variant="outline"
                disabled={auth.isPending}
                onClick={() => auth.mutate({ action: "logout" })}
              >
                {t("Log out")}
              </Button>
            )}
          </SettingsRow>
        )}
      {(status?.state === "auth_required" || reauth) && method && (
        <>
          <SettingsChoice
            label={t("Login method")}
            description={method.description ?? undefined}
            value={method.id}
            disabled={auth.isPending}
            options={methods.map(({ id, name }) => ({
              value: id,
              label: name,
            }))}
            onValueChange={setMethod}
          />
          {method.kind === "terminal" ? (
            <SettingsRow
              label={t("Terminal login")}
              description={
                <>
                  {t(
                    "Use a terminal on the Host to sign in, then check the connection again.",
                  )}{" "}
                  <code style={{ overflowWrap: "anywhere" }}>
                    {terminalCommand}
                  </code>
                  {method.id === "agy-terminal" && (
                    <span className="mt-1 block">
                      {t(
                        "Open the Google sign-in URL in a browser, then paste the callback into the same terminal.",
                      )}
                    </span>
                  )}
                  {!!method.environment_keys?.length && (
                    <code> ({method.environment_keys.join(", ")})</code>
                  )}
                </>
              }
            >
              <Button
                variant="outline"
                onClick={() =>
                  void navigator.clipboard.writeText(terminalCommand)
                }
              >
                {t("Copy command")}
              </Button>
            </SettingsRow>
          ) : (
            <SettingsRow label={t("Agent account")}>
              <Button
                disabled={auth.isPending}
                onClick={() =>
                  auth.mutate({ action: "login", method_id: method.id })
                }
              >
                {method.kind === "browser"
                  ? t("Sign in with Google")
                  : t("Log in")}
              </Button>
            </SettingsRow>
          )}
        </>
      )}
      <ErrorNotice
        error={
          auth.error ||
          start.error ||
          probe.error ||
          (signingIn ? inputs.error : null)
        }
      />
      {signingIn &&
        inputs.data?.map((request) => (
          <McpInput
            key={request.request_id}
            threadId={request.thread_id}
            request={request}
            reconcile={() => void inputs.refetch()}
            submitResponse={(body) =>
              candidate
                ? result(
                    client.POST(
                      "/api/acp-agents/preset-auth/inputs/{request_id}/response",
                      {
                        params: { path: { request_id: request.request_id } },
                        body,
                      },
                    ),
                  )
                : result(
                    client.POST(
                      "/api/acp-agents/{agent_id}/auth/inputs/{request_id}/response",
                      {
                        params: {
                          path: {
                            agent_id: agentId!,
                            request_id: request.request_id,
                          },
                        },
                        body,
                      },
                    ),
                  )
            }
          />
        ))}
    </>
  );
  return candidate ? (
    content
  ) : (
    <SettingsSection title={t("Authentication")}>{content}</SettingsSection>
  );
}
