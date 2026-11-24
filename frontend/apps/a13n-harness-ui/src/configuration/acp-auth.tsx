import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useState } from "react";
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

export function AcpAuth({ agentId }: { agentId: string }) {
  const { t } = useTranslation();
  const { client } = useTransport();
  const [status, setStatus] = useState<Schema<"AcpAuthStatus">>();
  const [methodId, setMethod] = useState("");
  const params = { path: { agent_id: agentId } };
  const auth = useMutation({
    mutationFn: (body: Schema<"AcpAuthRequest">) =>
      result(client.POST("/api/acp-agents/{agent_id}/auth", { params, body })),
    onSuccess: (response) => {
      setStatus(response);
      setMethod((previous) =>
        response.methods?.some(({ id }) => id === previous)
          ? previous
          : (response.methods?.[0]?.id ?? ""),
      );
    },
  });
  const method = status?.methods?.find(({ id }) => id === methodId);
  const signingIn = auth.isPending && auth.variables?.action === "login";
  const inputs = useQuery({
    queryKey: ["acp-auth-inputs", agentId],
    enabled: signingIn,
    refetchInterval: signingIn ? 800 : false,
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/acp-agents/{agent_id}/auth/inputs", {
          params,
          signal,
        }),
      ),
  });
  const message = !status
    ? t("Check the saved executable and discover its login methods.")
    : {
        executable_missing: t("The configured executable was not found."),
        available: t("Agent connected. Login status is unknown."),
        login_completed: t("The Agent confirmed login."),
        logout_completed: t("The Agent confirmed logout."),
      }[status.state];
  return (
    <SettingsSection title={t("Authentication")}>
      <SettingsRow label={t("ACP connection")} description={message}>
        <Button
          variant="outline"
          disabled={auth.isPending}
          onClick={() => auth.mutate({ action: "probe" })}
        >
          {t("Check connection")}
        </Button>
      </SettingsRow>
      {!!status?.methods?.length && (
        <>
          <SettingsChoice
            label={t("Login method")}
            description={method?.description ?? undefined}
            value={methodId}
            disabled={auth.isPending}
            options={status.methods.map(({ id, name }) => ({
              value: id,
              label: name,
            }))}
            onValueChange={setMethod}
          />
          {method?.kind === "terminal" && (
            <>
              <SettingsRow
                label={t("Terminal login")}
                description={t(
                  "Use a terminal on the Host to sign in, then check the connection again.",
                )}
              >
                <code style={{ overflowWrap: "anywhere" }}>
                  {JSON.stringify(method.argv)}
                </code>
              </SettingsRow>
              {!!method.environment_keys?.length && (
                <SettingsRow label={t("Environment variables")}>
                  <code>{method.environment_keys.join(", ")}</code>
                </SettingsRow>
              )}
            </>
          )}
        </>
      )}
      {(method?.kind === "agent" || status?.logout_supported) && (
        <SettingsRow label={t("Agent account")}>
          {method?.kind === "agent" && (
            <Button
              disabled={auth.isPending}
              onClick={() =>
                auth.mutate({ action: "login", method_id: method.id })
              }
            >
              {t("Log in")}
            </Button>
          )}
          {status?.logout_supported && (
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
      <ErrorNotice error={auth.error || (signingIn ? inputs.error : null)} />
      {signingIn &&
        inputs.data?.map((request) => (
          <McpInput
            key={request.request_id}
            threadId={request.thread_id}
            request={request}
            reconcile={() => void inputs.refetch()}
            submitResponse={(body) =>
              result(
                client.POST(
                  "/api/acp-agents/{agent_id}/auth/inputs/{request_id}/response",
                  {
                    params: {
                      path: {
                        agent_id: agentId,
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
    </SettingsSection>
  );
}
