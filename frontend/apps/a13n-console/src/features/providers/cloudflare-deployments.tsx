import {
  Button,
  FormField,
  Input,
  SegmentedControl,
  StatusPill,
} from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useEffect, useState, type ReactNode } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, type Schema } from "../../shared/api";
import { Confirm } from "../../shared/dialogs";
import { ErrorNotice } from "../../shared/feedback";
import { FormActions, FormSection } from "../../shared/forms";
import { ProviderEditor, ProviderName } from "./provider-editor";
import styles from "./providers.module.css";

type Deployment = Schema["Deployment"];
const collection = "/api/v1/cloudflare-deployments";
const active = (item: Deployment) =>
  ["deploying", "retiring"].includes(item.status);
const header = (item: Deployment) => ifMatch(`"${item.id}:${item.version}"`);

export function useCloudflareDeployments(enabled: boolean) {
  const client = useClient();
  const cache = useQueryClient();
  const { workspace, can } = useWorkspace();
  const key = ["cloudflare-deployments", workspace.id];
  const http = client.workspace(workspace.id);
  const query = useQuery({
    queryKey: key,
    queryFn: ({ signal }) => http.GET(collection, { signal }).then(data),
    enabled: enabled && can("admin"),
    refetchInterval: (state) =>
      state.state.data?.items.some(active) ? 2000 : false,
  });
  // A durable transition can happen after a dialog closes or another replica acts.
  const published = query.data?.items
    .filter((item) => item.status === "ready")
    .map((item) => item.provider_id)
    .sort()
    .join();
  useEffect(() => {
    if (!enabled || published === undefined) return;
    void cache.invalidateQueries({
      queryKey: ["environment-providers", workspace.id],
    });
    void cache.invalidateQueries({
      queryKey: ["environment-provider-options", workspace.id],
    });
  }, [cache, enabled, published, workspace.id]);
  function refresh() {
    return Promise.all([
      cache.invalidateQueries({ queryKey: key }),
      cache.invalidateQueries({
        queryKey: ["environment-providers", workspace.id],
      }),
      cache.invalidateQueries({
        queryKey: ["environment-provider-options", workspace.id],
      }),
    ]);
  }
  return { query, http, refresh };
}

export function CloudflareSetup({
  available,
  external,
  close,
}: {
  available: boolean;
  external: ReactNode;
  close: () => void;
}) {
  const { t } = useTranslation();
  const [mode, setMode] = useState<"managed" | "existing">(
    available ? "managed" : "existing",
  );
  return (
    <div className={styles.cloudflareSetup}>
      <SegmentedControl
        label={t("Cloudflare setup method")}
        value={mode}
        onValueChange={(value) =>
          setMode(value === "managed" ? "managed" : "existing")
        }
        options={[
          {
            value: "managed",
            label: t("Deploy automatically"),
            disabled: !available,
          },
          { value: "existing", label: t("Use existing gateway") },
        ]}
      />
      {mode === "existing" ? (
        <>
          <p className={styles.cloudflareDescription}>
            {t(
              "Enter the HTTPS URL and token for your deployed a13n Cloudflare gateway. Do not use a Cloudflare API token here.",
            )}
          </p>
          {external}
        </>
      ) : (
        <ManagedForm close={close} />
      )}
    </div>
  );
}

function ManagedForm({ close }: { close: () => void }) {
  const { t } = useTranslation();
  const { http, refresh } = useCloudflareDeployments(false);
  const [name, setName] = useState("");
  const [account, setAccount] = useState("");
  const [token, setToken] = useState("");
  const [request, setRequest] = useState(() => crypto.randomUUID());
  const deploy = useMutation({
    mutationFn: (body: Schema["DeploymentCreate"]) =>
      http.POST(collection, { body }).then(data),
    gcTime: 0,
    onSuccess: () => {
      void refresh();
      close();
    },
  });
  return (
    <ProviderEditor
      onSubmit={(event) => {
        event.preventDefault();
        if (!token || deploy.isPending) return;
        deploy.mutate({
          name: name.trim(),
          account_id: account.trim().toLowerCase(),
          api_token: token.trim(),
          request_id: request,
        });
        setToken("");
      }}
    >
      <p className={styles.cloudflareDescription}>
        {t(
          "Service deploys a dedicated gateway into your Cloudflare account and verifies it before adding the provider.",
        )}
      </p>
      <ProviderName
        value={name}
        onChange={(value) => {
          setName(value);
          setRequest(crypto.randomUUID());
        }}
      />
      <FormSection title={t("Cloudflare account")}>
        <FormField
          label={t("Account ID")}
          description={t(
            "Find the 32-character ID in your Cloudflare dashboard.",
          )}
        >
          <Input
            value={account}
            onChange={(event) => {
              setAccount(event.target.value);
              setRequest(crypto.randomUUID());
            }}
            required
            pattern="[A-Fa-f0-9]{32}"
            spellCheck={false}
          />
        </FormField>
        <FormField
          label={t("Cloudflare API token")}
          description={t(
            "Use an account-scoped token with Workers Scripts and Containers write access. Service stores it encrypted for retries and cleanup.",
          )}
        >
          <Input
            type="password"
            autoComplete="new-password"
            maxLength={512}
            required
            value={token}
            onChange={(event) => setToken(event.target.value)}
          />
        </FormField>
      </FormSection>
      <ErrorNotice error={deploy.error} />
      <FormActions
        label={t("Deploy gateway")}
        pending={deploy.isPending}
        disabled={!name.trim() || !token.trim()}
        onCancel={close}
      />
    </ProviderEditor>
  );
}

const deploymentState = {
  deploying: ["Deploying", "info"],
  ready: ["Ready", "success"],
  retiring: ["Removing", "info"],
  needs_attention: ["Needs attention", "warning"],
  retired: ["Removed", "neutral"],
} as const;
const stages: Record<string, string> = {
  preflight: "Checking Cloudflare account",
  image: "Copying sandbox image",
  worker: "Deploying Worker and Container application",
  smoke: "Checking container connectivity",
  remove_smoke: "Removing the verification container",
  publish: "Adding the environment provider",
};
const cleanupStages: Record<string, string> = {
  smoke: "Removing the verification container",
  application: "Removing the Container application",
  namespace: "Removing the Durable Object namespace",
  worker: "Removing the Worker",
  image: "Removing the registry tag",
  finish: "Removing the environment provider",
};
const failures: Record<string, string> = {
  cloudflare_authorization_required:
    "Cloudflare rejected the API token. Replace it with a token that can manage this account.",
  workers_subdomain_required:
    "Enable a workers.dev subdomain in your Cloudflare account, then retry.",
  deployment_tool_unavailable:
    "The Service operator must install and configure the deployment tools before retrying.",
  worker_ownership_mismatch:
    "The Worker no longer matches this deployment. Check the resource in Cloudflare before retrying.",
  image_ownership_mismatch:
    "The registry tag no longer matches this deployment. Check the resource in Cloudflare before retrying.",
};

export function CloudflareDeployments({
  controller,
}: {
  controller: ReturnType<typeof useCloudflareDeployments>;
}) {
  const { t } = useTranslation();
  const items = controller.query.data?.items ?? [];
  return (
    <>
      <ErrorNotice
        error={controller.query.error}
        retry={() => void controller.query.refetch()}
      />
      {items.length > 0 && (
        <section
          className={styles.cloudflareDeployments}
          aria-label={t("Managed Cloudflare gateways")}
        >
          <h2>{t("Managed Cloudflare gateways")}</h2>
          <ul>
            {items.map((item) => (
              <DeploymentItem
                key={item.id}
                item={item}
                controller={controller}
              />
            ))}
          </ul>
        </section>
      )}
    </>
  );
}

function DeploymentItem({
  item,
  controller,
}: {
  item: Deployment;
  controller: ReturnType<typeof useCloudflareDeployments>;
}) {
  const { t } = useTranslation();
  const { basePath } = useWorkspace();
  const { http, refresh, query } = controller;
  const params = { path: { deployment_id: item.id } };
  const [token, setToken] = useState("");
  const [retryOpen, setRetryOpen] = useState(false);
  const retry = useMutation({
    mutationFn: (api_token: string) =>
      http
        .POST(`${collection}/{deployment_id}/retry`, {
          params,
          headers: header(item),
          body: api_token ? { api_token } : {},
        })
        .then(data),
    gcTime: 0,
    onSuccess: () => {
      setRetryOpen(false);
      void refresh();
    },
  });
  const [status, variant] = deploymentState[item.status];
  const description = (item.operation === "deploy" ? stages : cleanupStages)[
    item.stage
  ];
  const available = query.data?.available === true;
  const canRetire =
    available &&
    item.operation === "deploy" &&
    ["ready", "needs_attention"].includes(item.status);
  return (
    <li className={styles.cloudflareDeployment}>
      <div className={styles.cloudflareDeploymentIdentity}>
        <div>
          <strong>{item.name}</strong>
          <StatusPill variant={variant}>{t(status)}</StatusPill>
        </div>
        <p>
          {item.status === "ready"
            ? t("Verified gateway and sandbox connectivity")
            : t(description ?? "Waiting for Service")}
        </p>
        {item.error && item.status === "needs_attention" && (
          <p role="status">
            {t(
              failures[item.error] ??
                "This step did not complete. Retry to continue.",
            )}{" "}
            <code>{item.error}</code>
          </p>
        )}
        {item.operation === "retire" && (
          <p>
            {t("Cleanup confirmed for {{count}} of {{total}} resources", {
              count: Object.values(item.cleanup).filter(
                (state) => state !== "pending",
              ).length,
              total: Object.keys(item.cleanup).length,
            })}
          </p>
        )}
      </div>
      <div className={styles.cloudflareDeploymentActions}>
        {item.provider_id && item.status === "ready" && (
          <Button
            size="sm"
            variant="outline"
            render={
              <Link
                to={`${basePath}/environments/new?provider=${encodeURIComponent(item.provider_id)}`}
              />
            }
          >
            {t("Create template")}
          </Button>
        )}
        {available && item.status === "needs_attention" && !retryOpen && (
          <Button
            size="sm"
            variant="outline"
            onClick={() => setRetryOpen(true)}
          >
            {t("Retry deployment step")}
          </Button>
        )}
        {canRetire && (
          <Confirm
            title={t("Remove gateway")}
            trigger={t("Remove gateway")}
            triggerVariant="ghost"
            danger
            subject={item.name}
            description={t(
              "Remove the managed Cloudflare resources and their provider. Delete any environments and templates using it first.",
            )}
            action={() =>
              http
                .POST(`${collection}/{deployment_id}/retire`, {
                  params,
                  headers: header(item),
                })
                .then(data)
            }
            onSuccess={() => void refresh()}
          />
        )}
      </div>
      {available && item.status === "needs_attention" && retryOpen && (
        <form
          className={styles.cloudflareRetry}
          onSubmit={(event) => {
            event.preventDefault();
            if (retry.isPending) return;
            retry.mutate(token.trim());
            setToken("");
          }}
        >
          <FormField label={t("New Cloudflare API token (optional)")}>
            <Input
              type="password"
              value={token}
              autoComplete="new-password"
              maxLength={512}
              onChange={(event) => setToken(event.target.value)}
            />
          </FormField>
          <ErrorNotice error={retry.error} />
          <FormActions
            label={t("Retry")}
            pending={retry.isPending}
            onCancel={() => {
              setToken("");
              setRetryOpen(false);
            }}
          />
        </form>
      )}
    </li>
  );
}
