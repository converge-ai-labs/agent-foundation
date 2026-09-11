import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ReadOnlyField } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";

type ClientInput = Schema["MCPOAuthClientInput"];
type AuthMethod = ClientInput["token_endpoint_auth_method"];

export function MCPOAuthClientEditor({
  connection,
  onSaved,
  onCancel,
}: {
  connection: Schema["MCPConnection"];
  onSaved: (connection: Schema["MCPConnection"]) => void;
  onCancel: () => void;
}) {
  const client = useClient();
  const query = useQuery({
    queryKey: ["mcp-oauth-client", connection.id, connection.version],
    queryFn: async ({ signal }) => {
      const params = { path: { connection_id: connection.id } };
      const [configuration, discovery] = await Promise.all([
        client.http
          .GET("/api/v1/mcp-connections/{connection_id}/oauth-client", {
            params,
            signal,
          })
          .then(data),
        client.http
          .POST("/api/v1/mcp-connections/{connection_id}/oauth-discovery", {
            params,
            signal,
          })
          .then(data),
      ]);
      return { configuration, discovery };
    },
  });
  if (query.isPending) return <Loading />;
  if (query.error)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  return (
    <ClientForm
      connection={connection}
      {...query.data}
      onSaved={onSaved}
      onCancel={onCancel}
    />
  );
}

function ClientForm({
  connection,
  configuration,
  discovery,
  onSaved,
  onCancel,
}: {
  connection: Schema["MCPConnection"];
  configuration: Schema["MCPOAuthClientConfiguration"] | null;
  discovery: Schema["MCPOAuthDiscovery"];
  onSaved: (connection: Schema["MCPConnection"]) => void;
  onCancel: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation();
  const supported = discovery.token_endpoint_auth_methods_supported;
  const [clientId, setClientId] = useState(configuration?.client_id ?? "");
  const [secret, setSecret] = useState("");
  const [method, setMethod] = useState<AuthMethod>(
    configuration?.token_endpoint_auth_method ?? supported[0] ?? "none",
  );
  const save = useMutation({
    gcTime: 0,
    mutationFn: (remove: boolean) =>
      client.http
        .PUT("/api/v1/mcp-connections/{connection_id}/oauth-client", {
          params: { path: { connection_id: connection.id } },
          body: {
            expected_version: connection.version,
            client: remove
              ? null
              : {
                  issuer_url: discovery.issuer_url,
                  client_id: clientId,
                  token_endpoint_auth_method: method,
                  client_secret: method === "none" ? null : secret,
                },
          },
        })
        .then(data),
    onSuccess: (updated) => {
      setSecret("");
      void cache.invalidateQueries({ queryKey: ["mcp-connections"] });
      void cache.invalidateQueries({
        queryKey: ["mcp-oauth-client", connection.id],
      });
      onSaved(updated);
    },
  });
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        save.mutate(false);
      }}
    >
      <p className={styles.muted}>
        {t(
          "Register an OAuth app with the provider using this redirect URI, then enter its client credentials.",
        )}
      </p>
      <ReadOnlyField label={t("Authorization server")}>
        <span className="break-all">{discovery.issuer_url}</span>
      </ReadOnlyField>
      <FormField label={t("Redirect URI")}>
        <Input
          readOnly
          value={discovery.redirect_uri}
          onFocus={(event) => event.target.select()}
        />
      </FormField>
      <FormField label={t("Client ID")}>
        <Input
          required
          maxLength={2048}
          value={clientId}
          onChange={(event) => setClientId(event.target.value)}
        />
      </FormField>
      <ChoiceField
        label={t("Client authentication")}
        placeholder={t("Select authentication")}
        value={method}
        options={supported.map((value) => ({
          value,
          label: t(
            {
              none: "Public client (no secret)",
              client_secret_basic: "Client secret in Basic header",
              client_secret_post: "Client secret in request body",
            }[value],
          ),
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
            autoComplete="off"
            maxLength={16384}
            value={secret}
            onChange={(event) => setSecret(event.target.value)}
          />
        </FormField>
      )}
      <p className={styles.muted}>
        {t(
          "Saving an OAuth app clears existing tokens and requires authorization again.",
        )}
      </p>
      <ErrorNotice error={save.error} />
      <FormActions
        pending={save.isPending}
        label={t("Save and authorize")}
        disabled={!supported.length}
      />
      <div className={styles.actions}>
        <Button
          type="button"
          variant="ghost"
          disabled={save.isPending}
          onClick={onCancel}
        >
          {t("Cancel")}
        </Button>
        {configuration && (
          <Button
            type="button"
            variant="outline"
            disabled={save.isPending}
            onClick={() => save.mutate(true)}
          >
            {t("Use automatic client registration")}
          </Button>
        )}
      </div>
    </form>
  );
}
