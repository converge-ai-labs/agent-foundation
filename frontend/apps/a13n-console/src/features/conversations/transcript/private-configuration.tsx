import Ajv from "ajv/dist/2020";
import { Button, FormField, Input } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useRef, useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import schemas from "../../../service-client/composer-tools.json";
import { data, ifMatch, type Schema } from "../../../shared/api";
import { ErrorNotice } from "../../../shared/feedback";
import { SecretReveal } from "../../../shared/forms";
import { credentialKeys, credentialQueries } from "../../credentials/api";
import { validToken } from "../../credentials/draft";
import { webhookKeys, webhookQueries } from "../../settings/webhooks/api";
import styles from "./cards.module.css";

export type ConfigurationTool =
  "configure_webhook" | "configure_egress_credential";
type Versioned = { expected_version: string };
type WebhookProposal =
  | ({ action: "create" } & Omit<
      Schema["SubscriptionCreate"],
      "signing_secret"
    >)
  | ({ action: "rotate"; subscription_id: string } & Versioned);
type CredentialProposal =
  | ({ action: "create" } & Omit<Schema["CredentialCreate"], "value">)
  | ({ action: "update"; credential_id: string } & Versioned &
      Schema["CredentialUpdate"])
  | ({ action: "rotate" | "revoke"; credential_id: string } & Versioned)
  | { action: "sync"; credential_id: string };
type Result =
  | {
      status: "saved";
      subscription_id?: string;
      credential_id?: string;
      version?: number;
      credential_version?: number;
    }
  | { status: "cancelled" | "unknown" };
const ajv = new Ajv({ strict: false });
const validators = {
  configure_webhook: ajv.compile<{ request: WebhookProposal }>(
    schemas.configure_webhook,
  ),
  configure_egress_credential: ajv.compile<{ request: CredentialProposal }>(
    schemas.configure_egress_credential,
  ),
};

/** Validate against the same generated schemas the model receives; never accept secret arguments. */
export function PrivateConfiguration({
  tool,
  arguments: input,
  onResolved,
}: {
  tool: ConfigurationTool;
  arguments: Schema["JsonValue"];
  onResolved: (result: Result) => void;
}) {
  const { t } = useTranslation();
  const [cancelled, setCancelled] = useState(false);
  const validate = validators[tool];
  if (!validate(input))
    return (
      <>
        <p>
          {t(
            "This configuration request is invalid. Cancel it and ask for a new request.",
          )}
        </p>
        <Button
          type="button"
          variant="outline"
          disabled={cancelled}
          onClick={() => {
            setCancelled(true);
            onResolved({ status: "cancelled" });
          }}
        >
          {t("Cancel configuration")}
        </Button>
      </>
    );
  return (
    <ConfigurationFields
      tool={tool}
      request={input.request}
      onResolved={onResolved}
    />
  );
}

function ConfigurationFields({
  tool,
  request,
  onResolved,
}: {
  tool: ConfigurationTool;
  request: WebhookProposal | CredentialProposal;
  onResolved: (result: Result) => void;
}) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace, basePath, can } = useWorkspace();
  const cache = useQueryClient();
  const http = client.workspace(workspace.id);
  const webhook = tool === "configure_webhook";
  const id =
    "subscription_id" in request
      ? request.subscription_id
      : "credential_id" in request
        ? request.credential_id
        : undefined;
  const permitted = can("run") && can(webhook ? "admin" : "write");
  const credential = useQuery({
    ...credentialQueries(client, workspace.id).item(id ?? ""),
    enabled: permitted && !webhook && !!id,
  });
  const subscription = useQuery({
    ...webhookQueries(client, workspace.id).subscription(id ?? ""),
    enabled: permitted && webhook && !!id,
  });
  const ready = !id || (webhook ? !!subscription.data : !!credential.data);
  const managed =
    !webhook && !!credential.data?.owner_app_id && request.action !== "sync";
  const revoked =
    !webhook &&
    credential.data?.state === "revoked" &&
    ["rotate", "revoke"].includes(request.action);
  const [secret, setSecret] = useState("");
  const [generated, setGenerated] = useState("");
  const [result, setResult] = useState<Result | null>(null);
  // A synchronous guard covers double clicks and every ambiguous HTTP outcome.
  const attempted = useRef(false);
  const needsSecret =
    request.action === "create" || request.action === "rotate";
  const path = `${basePath}/${webhook ? "settings/webhooks" : "credentials"}${id ? `/${id}` : ""}`;
  function resolve(value: Result) {
    setSecret("");
    setGenerated("");
    setResult(value);
    onResolved(value);
  }
  const save = useMutation({
    retry: false,
    gcTime: 0,
    mutationFn: async (): Promise<Result> => {
      if (
        !permitted ||
        !ready ||
        managed ||
        revoked ||
        attempted.current ||
        result
      )
        throw new Error(
          t("Configuration is unavailable. Review the resource page."),
        );
      if (
        needsSecret &&
        (webhook
          ? !validSigningSecret(secret, request.action === "create")
          : !validToken(secret))
      )
        throw new Error(t("Enter a valid secret before saving."));
      attempted.current = true;
      try {
        if (webhook) {
          const proposal = request as WebhookProposal;
          if (proposal.action === "create") {
            const { action: _, ...body } = proposal;
            const { signing_secret, id, version } = data(
              await http.POST("/api/v1/subscriptions", {
                body: {
                  ...body,
                  ...(secret ? { signing_secret: secret } : {}),
                },
              }),
            );
            setGenerated(signing_secret);
            return { status: "saved", subscription_id: id, version };
          }
          const saved = data(
            await http.PATCH("/api/v1/subscriptions/{subscription_id}", {
              params: { path: { subscription_id: proposal.subscription_id } },
              headers: versionHeaders(proposal.expected_version),
              body: { signing_secret: secret },
            }),
          );
          return {
            status: "saved",
            subscription_id: saved.id,
            version: saved.version,
          };
        }
        const proposal = request as CredentialProposal;
        if (proposal.action === "sync") {
          data(
            await http.POST("/api/v1/egress-credentials/{credential_id}/sync", {
              params: { path: { credential_id: proposal.credential_id } },
            }),
          );
          return { status: "saved", credential_id: proposal.credential_id };
        }
        let saved: Schema["Credential"];
        if (proposal.action === "create") {
          const { action: _, ...body } = proposal;
          saved = data(
            await http.POST("/api/v1/egress-credentials", {
              body: { ...body, value: secret },
            }),
          );
        } else {
          const params = { path: { credential_id: proposal.credential_id } };
          const headers = versionHeaders(proposal.expected_version);
          if (proposal.action === "rotate")
            saved = data(
              await http.PUT(
                "/api/v1/egress-credentials/{credential_id}/value",
                { params, headers, body: { value: secret } },
              ),
            );
          else if (proposal.action === "revoke")
            saved = data(
              await http.POST(
                "/api/v1/egress-credentials/{credential_id}/revoke",
                { params, headers },
              ),
            );
          else {
            const {
              action: _,
              credential_id: _id,
              expected_version: _version,
              ...body
            } = proposal;
            saved = data(
              await http.PATCH("/api/v1/egress-credentials/{credential_id}", {
                params,
                headers,
                body,
              }),
            );
          }
        }
        return {
          status: "saved",
          credential_id: saved.id,
          version: saved.version,
          credential_version: saved.credential_version,
        };
      } catch {
        // Never retain server errors, request bodies or secret-bearing objects in mutation state.
        throw new Error(
          t(
            "Save could not be confirmed. Review the resource page and ask for a fresh request before another write.",
          ),
        );
      } finally {
        setSecret("");
      }
    },
    onSuccess: (value) => {
      setResult(value);
      if (!(webhook && request.action === "create")) onResolved(value);
      void cache.invalidateQueries({
        queryKey: webhook
          ? webhookKeys(workspace.id).root
          : credentialKeys(workspace.id).root,
      });
    },
  });
  return (
    <div className="grid gap-4">
      <dl className={styles.cardFacts}>
        <dt>{t("Action")}</dt>
        <dd>{t(actionLabels[request.action])}</dd>
        <dt>{t("Name")}</dt>
        <dd>
          {"name" in request && request.name != null
            ? request.name
            : ((webhook ? subscription.data?.name : credential.data?.name) ??
              id)}
        </dd>
        {"description" in request && request.description != null && (
          <>
            <dt>{t("Description")}</dt>
            <dd>{request.description || "—"}</dd>
          </>
        )}
        {"url" in request && (
          <>
            <dt>{t("Endpoint URL")}</dt>
            <dd className="break-all">{request.url}</dd>
          </>
        )}
        {"allowed_hosts" in request && (
          <>
            <dt>{t("Allowed domains")}</dt>
            <dd className="break-all">{request.allowed_hosts.join(", ")}</dd>
          </>
        )}
        {credential.data && (
          <>
            <dt>{t("Allowed domains")}</dt>
            <dd className="break-all">
              {credential.data.allowed_hosts.join(", ")}
            </dd>
          </>
        )}
        {"kinds" in request && (
          <>
            <dt>{t("Events")}</dt>
            <dd className="break-all">{request.kinds.join(", ")}</dd>
            <dt>{t("Status")}</dt>
            <dd>{t(request.enabled === false ? "Disabled" : "Enabled")}</dd>
            <dt>{t("Matching conditions")}</dt>
            <dd>
              {Object.entries(request.filter ?? {})
                .filter(([, value]) => value)
                .map(([key, value]) => `${key}: ${value}`)
                .join(", ") || t("Entire workspace")}
            </dd>
          </>
        )}
        {id && (
          <>
            <dt>{t("Resource ID")}</dt>
            <dd className="break-all">{id}</dd>
          </>
        )}
      </dl>
      <p className="text-sm text-muted-foreground">
        {t(
          "Secrets go directly to Service. The agent receives only the result and resource identifiers.",
        )}
      </p>
      {!webhook && (
        <p className="text-sm text-muted-foreground">
          {t(
            request.action === "revoke"
              ? "Prevents new use and removes the stored token. Active Sessions remove it asynchronously. This cannot be undone."
              : "Active Sessions update asynchronously; idle Sessions update on next use.",
          )}
        </p>
      )}
      {!webhook && request.action === "revoke" && (
        <p>
          {t(
            "Idle Sessions may retain the old token until next use or expiry. This does not revoke the token at its upstream provider.",
          )}
        </p>
      )}
      {webhook && (
        <p className="text-sm text-muted-foreground">
          {t(
            "Only matching lifecycle changes generate notifications. Changes affect newly queued deliveries.",
          )}
        </p>
      )}
      <a
        className="text-sm underline underline-offset-4"
        href={path}
        target="_blank"
        rel="noreferrer"
      >
        {t(webhook ? "Open Webhook" : "Credentials")}
      </a>
      {generated ? (
        <>
          <SecretReveal value={generated} label={t("Signing secret")} wrap />
          <p>
            {t(
              "Verify webhook-signature with this secret and deduplicate by webhook-id.",
            )}
          </p>
          <Button
            type="button"
            disabled={!result}
            onClick={() => {
              if (result) resolve(result);
            }}
          >
            {t("I saved the secret")}
          </Button>
        </>
      ) : result ? (
        <p role="status">
          {t(
            result.status === "saved"
              ? "Saved. Submit responses to continue. Retrying that step will not repeat this action."
              : result.status === "unknown"
                ? "Save outcome unknown. Review the resource page before requesting another write."
                : "Configuration cancelled. Submit responses to continue.",
          )}
        </p>
      ) : (
        <>
          {(!permitted || managed || revoked) && (
            <p role="status">
              {t("Configuration is unavailable. Review the resource page.")}
            </p>
          )}
          {!ready && <p role="status">{t("Loading…")}</p>}
          {(credential.error || subscription.error) && (
            <p role="alert">
              {t("Configuration is unavailable. Review the resource page.")}
            </p>
          )}
          <fieldset
            className="grid gap-4"
            disabled={
              !permitted ||
              !ready ||
              managed ||
              revoked ||
              save.isPending ||
              attempted.current
            }
          >
            {needsSecret && (
              <FormField
                label={t(webhook ? "Signing secret" : "Token")}
                description={
                  webhook && request.action === "create"
                    ? t("Leave empty to generate a signing secret.")
                    : undefined
                }
              >
                <Input
                  type="password"
                  autoComplete="new-password"
                  value={secret}
                  onChange={(event) => setSecret(event.target.value)}
                />
              </FormField>
            )}
            <Button
              type="button"
              loading={save.isPending}
              onClick={() => save.mutate()}
            >
              {t(
                request.action === "revoke"
                  ? "Revoke credential"
                  : request.action === "sync"
                    ? "Sync active Sessions"
                    : "Save changes",
              )}
            </Button>
          </fieldset>
          <ErrorNotice error={save.error} />
          <Button
            type="button"
            variant="outline"
            disabled={save.isPending}
            onClick={() =>
              resolve({ status: attempted.current ? "unknown" : "cancelled" })
            }
          >
            {t("Cancel configuration")}
          </Button>
        </>
      )}
    </div>
  );
}

function versionHeaders(version: string) {
  return ifMatch(version.startsWith('"') ? version : `"${version}"`);
}
function validSigningSecret(value: string, optional: boolean) {
  if (!value) return optional;
  if (!/^whsec_[A-Za-z0-9+/]+={0,2}$/.test(value)) return false;
  try {
    const decoded = atob(value.slice(6));
    return decoded.length >= 24 && decoded.length <= 64;
  } catch {
    return false;
  }
}

const actionLabels = {
  create: "Create",
  update: "Edit",
  rotate: "Rotate secret",
  revoke: "Revoke credential",
  sync: "Sync active Sessions",
};
