import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ReadOnlyField } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { CopyButton } from "../../shared/copy";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";

type ClientInput = Schema["MCPOAuthClientInput"];
type AuthMethod = ClientInput["token_endpoint_auth_method"];
type GrantType = ClientInput["grant_type"];

const authLabels = {
  none: "Public client (no secret)",
  client_secret_basic: "Client secret in Basic header",
  client_secret_post: "Client secret in request body",
} as const;

export function MCPOAuthClientEditor({
  connection,
  configuration,
  discovery,
  onSaved,
  onCancel,
}: {
  connection: Schema["Connection"];
  configuration: Schema["MCPOAuthClientConfiguration"] | null;
  discovery: Schema["MCPOAuthDiscovery"];
  onSaved: (connection: Schema["Connection"], grant: GrantType) => void;
  onCancel?: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    supportedGrants = discovery.grant_types_supported,
    initialGrant =
      configuration?.grant_type ?? supportedGrants[0] ?? "authorization_code";
  const [grant, setGrant] = useState<GrantType>(initialGrant),
    [clientId, setClientId] = useState(configuration?.client_id ?? ""),
    [secret, setSecret] = useState("");
  const methods = discovery.token_endpoint_auth_methods_supported.filter(
    (method) => grant === "authorization_code" || method !== "none",
  );
  const [method, setMethod] = useState<AuthMethod>(
    configuration?.token_endpoint_auth_method ??
      (initialGrant === "client_credentials"
        ? (methods.find((value) => value !== "none") ?? "client_secret_basic")
        : (methods[0] ?? "none")),
  );
  const save = useMutation({
    gcTime: 0,
    mutationFn: (remove: boolean) =>
      client.http
        .PUT("/api/v1/connections/{connection_id}/mcp/oauth-client", {
          params: { path: { connection_id: connection.id } },
          body: {
            expected_version: connection.version,
            client: remove
              ? null
              : {
                  issuer_url: discovery.issuer_url,
                  client_id: clientId,
                  token_endpoint_auth_method: method,
                  grant_type: grant,
                  client_secret: method === "none" ? null : secret,
                },
          },
        })
        .then(data),
    onSuccess: (updated, remove) => {
      setSecret("");
      void cache.invalidateQueries({ queryKey: ["connections"] });
      void cache.invalidateQueries({
        queryKey: ["mcp-oauth-setup", connection.id],
      });
      onSaved(updated, remove ? "authorization_code" : grant);
    },
  });
  const chooseGrant = (value: string | null) => {
    if (value !== "authorization_code" && value !== "client_credentials")
      return;
    setGrant(value);
    const compatible = discovery.token_endpoint_auth_methods_supported.filter(
      (candidate) => value === "authorization_code" || candidate !== "none",
    );
    if (!compatible.includes(method))
      setMethod(compatible[0] ?? "client_secret_basic");
    setSecret("");
  };
  return (
    <form
      className={styles.form}
      autoComplete="off"
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(false);
      }}
    >
      <div className="grid gap-1">
        <h3 className="text-sm font-medium">{t("Use your own OAuth app")}</h3>
        <p className={styles.muted}>
          {t(
            grant === "authorization_code"
              ? "Register an OAuth app with the provider, then enter its client credentials."
              : "Use a machine account that supports the OAuth client credentials grant.",
          )}
        </p>
      </div>
      {supportedGrants.length > 1 && (
        <ChoiceField
          label={t("OAuth grant")}
          placeholder={t("Select OAuth grant")}
          value={grant}
          options={supportedGrants.map((value) => ({
            value,
            label: t(
              value === "authorization_code"
                ? "User authorization"
                : "Machine account",
            ),
          }))}
          onValueChange={chooseGrant}
        />
      )}
      <ReadOnlyField label={t("Authorization server")}>
        <span className="break-all">{discovery.issuer_url}</span>
      </ReadOnlyField>
      {grant === "authorization_code" && discovery.redirect_uri && (
        <ReadOnlyField
          label={t("Callback URL")}
          description={t(
            "Add this exact callback URL to the OAuth app at the provider.",
          )}
        >
          <span className="flex min-w-0 items-center gap-2">
            <span className="min-w-0 break-all">{discovery.redirect_uri}</span>
            <CopyButton
              value={discovery.redirect_uri}
              copyLabel={t("Copy callback URL")}
            />
          </span>
        </ReadOnlyField>
      )}
      <FormField label={t("Client ID")}>
        <Input
          autoFocus
          autoComplete="off"
          required
          maxLength={2048}
          value={clientId}
          onChange={(event) => setClientId(event.target.value)}
        />
      </FormField>
      {methods.length === 1 ? (
        <ReadOnlyField label={t("Client authentication")}>
          {t(authLabels[methods[0]])}
        </ReadOnlyField>
      ) : (
        <ChoiceField
          label={t("Client authentication")}
          placeholder={t("Select authentication")}
          value={method}
          options={methods.map((value) => ({
            value,
            label: t(authLabels[value]),
          }))}
          onValueChange={(value) => {
            if (
              value === "none" ||
              value === "client_secret_basic" ||
              value === "client_secret_post"
            ) {
              setMethod(value);
              setSecret("");
            }
          }}
        />
      )}
      {method !== "none" && (
        <FormField
          label={t("Client secret")}
          description={t(
            "Existing secrets are never displayed. Supply the complete secret.",
          )}
        >
          <Input
            required
            type="password"
            autoComplete="new-password"
            maxLength={16384}
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
          />
        </FormField>
      )}
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        label={t(
          grant === "client_credentials"
            ? "Save and connect"
            : "Save and authorize",
        )}
        disabled={!methods.length}
      />
      <div className={styles.actions}>
        {onCancel && (
          <Button
            type="button"
            variant="ghost"
            disabled={save.isPending}
            onClick={onCancel}
          >
            {t("Cancel")}
          </Button>
        )}
        {configuration?.source === "pre_registered" && (
          <Button
            type="button"
            variant="outline"
            disabled={save.isPending}
            onClick={() => save.mutate(true)}
          >
            {t("Use automatic setup")}
          </Button>
        )}
      </div>
    </form>
  );
}
