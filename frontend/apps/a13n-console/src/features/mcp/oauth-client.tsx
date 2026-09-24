import { useMutation, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, FormField, Input, ReadOnlyField } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import { connectionPath } from "../connections/api";
import { CallbackUrlField } from "../connections/callback-url";

type AuthMethod = Schema["ClientAuthentication"];
type GrantType = Schema["OAuthGrant"];

const grants = ["authorization_code", "client_credentials"] as const;
const authLabels = {
  none: "Public client (no secret)",
  client_secret_basic: "Client secret in Basic header",
  client_secret_post: "Client secret in request body",
} as const;
/** A machine account always authenticates its client with a secret. */
const methodsFor = (grant: GrantType) =>
  (["none", "client_secret_basic", "client_secret_post"] as const).filter(
    (method) => grant === "authorization_code" || method !== "none",
  );

export function MCPOAuthClientEditor({
  connection,
  config,
  onSaved,
  onCancel,
}: {
  connection: Schema["Connection"];
  config: Schema["McpConfig"];
  onSaved: (connection: Schema["Connection"], grant: GrantType) => void;
  onCancel?: () => void;
}) {
  const client = useClient(),
    cache = useQueryClient(),
    { t } = useTranslation(),
    saved = config.oauth,
    initialGrant = saved?.grant_type ?? "authorization_code";
  const [grant, setGrant] = useState<GrantType>(initialGrant),
    [clientId, setClientId] = useState(saved?.client_id ?? ""),
    [secret, setSecret] = useState(""),
    [method, setMethod] = useState<AuthMethod>(
      saved?.token_endpoint_auth_method ?? methodsFor(initialGrant)[0],
    );
  const methods = methodsFor(grant);
  // The Service keeps a stored secret while the server and client ID stay the same.
  const keepsSecret =
    connection.client_secret_configured && clientId === saved?.client_id;
  const save = useMutation({
    gcTime: 0,
    mutationFn: (remove: boolean) =>
      client.http
        .PATCH(
          "/api/v1/workspaces/{workspace_id}/connections/{connection_id}",
          {
            params: { path: connectionPath(connection) },
            headers: ifMatch(rowTag(connection)),
            body: {
              config: {
                ...config,
                // Without a client ID the Service registers a client itself.
                oauth: remove
                  ? { scopes: saved?.scopes ?? [] }
                  : {
                      ...saved,
                      client_id: clientId,
                      token_endpoint_auth_method: method,
                      grant_type: grant,
                    },
              },
              ...(!remove && method !== "none" && secret
                ? { client_secret: secret }
                : {}),
            },
          },
        )
        .then(data),
    onSuccess: (updated, remove) => {
      setSecret("");
      void cache.invalidateQueries({ queryKey: ["connections"] });
      onSaved(updated, remove ? "authorization_code" : grant);
    },
  });
  const chooseGrant = (value: string | null) => {
    if (value !== "authorization_code" && value !== "client_credentials")
      return;
    setGrant(value);
    const compatible = methodsFor(value);
    if (!compatible.includes(method)) setMethod(compatible[0]);
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
      <ChoiceField
        label={t("OAuth grant")}
        placeholder={t("Select OAuth grant")}
        value={grant}
        options={grants.map((value) => ({
          value,
          label: t(
            value === "authorization_code"
              ? "User authorization"
              : "Machine account",
          ),
        }))}
        onValueChange={chooseGrant}
      />
      {grant === "authorization_code" && (
        <CallbackUrlField
          description={t(
            "Add this exact callback URL to the OAuth app at the provider.",
          )}
        />
      )}
      <FormField label={t("Client ID")}>
        <Input
          autoFocus
          autoComplete="off"
          required
          maxLength={512}
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
            required={!keepsSecret}
            type="password"
            autoComplete="new-password"
            maxLength={4096}
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
        {saved?.client_id && (
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
