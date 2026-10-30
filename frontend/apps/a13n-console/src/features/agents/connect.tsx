import { ArrowSquareOutIcon, CodeIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Button, MenuItem, ModalFrame } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { allPages, data, type Schema } from "../../shared/api";
import { ErrorNotice, InlineLoading } from "../../shared/feedback";
import { CopyButton } from "../../shared/identity";
import { useCreateKey } from "../settings/create-key";
import { integrationPrompt } from "./connect-prompt";

const guideUrl = "https://a13n.dev/docs/a13n-service/connect-application/";

export function ConnectAgent({
  agent,
  trigger = "menu",
}: {
  agent: Schema["Agent"];
  trigger?: "menu" | "button";
}) {
  const { t } = useTranslation();
  const { workspace } = useWorkspace();
  const [open, setOpen] = useState(false);
  const [secret, setSecret] = useState<string>();
  const create = useCreateKey(setSecret);
  const close = () => {
    setOpen(false);
    setSecret(undefined);
    create.reset();
  };
  const prompt = integrationPrompt({
    serviceUrl: window.location.origin,
    workspaceId: workspace.id,
    agentId: agent.id,
    agentName: agent.name,
  });
  return (
    <ModalFrame
      open={open}
      onOpenChange={(value, details) => {
        if (create.isPending) {
          details.cancel();
          return;
        }
        if (value) setOpen(true);
        else close();
      }}
      size="lg"
      title={t("Connect to app")}
      description={`${agent.name} · ${workspace.name}`}
      closeLabel={t("Close")}
      trigger={
        trigger === "button" ? (
          <Button
            variant="outline"
            disabled={!!agent.archived_at || !agent.default_revision_id}
          >
            <CodeIcon aria-hidden="true" />
            {t("Connect to app")}
          </Button>
        ) : (
          <MenuItem
            nativeButton
            render={<button type="button" />}
            closeOnClick={false}
            disabled={!!agent.archived_at || !agent.default_revision_id}
          >
            <CodeIcon aria-hidden="true" />
            {t("Connect to app")}
          </MenuItem>
        )
      }
      footer={
        <div className="flex w-full items-center justify-between gap-3">
          <Button
            variant="ghost"
            size="sm"
            render={<a href={guideUrl} target="_blank" rel="noreferrer" />}
          >
            {t("Docs")}
            <ArrowSquareOutIcon aria-hidden="true" />
          </Button>
          <CopyButton
            value={prompt}
            copyLabel={t("Copy for agent")}
            variant="default"
          />
        </div>
      }
    >
      {open && (
        <div className="grid gap-5">
          <dl className="divide-y divide-border rounded-lg border border-border text-sm">
            <ConnectionValue
              label={t("Service URL")}
              value={window.location.origin}
            />
            <ConnectionValue label={t("Agent ID")} value={agent.id} />
          </dl>
          <section className="grid gap-2.5" aria-label={t("API key")}>
            {secret ? (
              <>
                <h3 className="text-sm font-medium">{t("API key")}</h3>
                <div className="flex min-w-0 items-center gap-3 rounded-lg border border-border px-3 py-2.5">
                  <code className="min-w-0 flex-1 break-all text-xs leading-relaxed">
                    {secret}
                  </code>
                  <CopyButton
                    value={secret}
                    iconOnly
                    copyLabel={t("Copy API key")}
                  />
                </div>
                <p className="text-xs text-muted-foreground">
                  {t("Copy now. This key is shown only once.")}
                </p>
              </>
            ) : (
              <KeySetup create={create} />
            )}
            <p className="text-xs text-muted-foreground">
              {t("Set A13N_API_KEY in your server environment.")}
            </p>
          </section>
          <details className="border-t border-border pt-3 text-xs text-muted-foreground">
            <summary>{t("Prompt preview")}</summary>
            <pre className="mt-3 max-h-48 overflow-auto whitespace-pre-wrap break-words text-xs leading-relaxed text-foreground">
              {prompt}
            </pre>
          </details>
        </div>
      )}
    </ModalFrame>
  );
}

function ConnectionValue({ label, value }: { label: string; value: string }) {
  const { t } = useTranslation();
  return (
    <div className="flex min-w-0 flex-wrap items-center gap-x-4 gap-y-1 px-3 py-2.5">
      <dt className="w-20 shrink-0 text-xs text-muted-foreground">{label}</dt>
      <dd className="flex min-w-0 flex-1 items-center gap-2">
        <code className="min-w-0 flex-1 break-all text-xs">{value}</code>
        <CopyButton
          value={value}
          iconOnly
          copyLabel={t("Copy {{label}}", { label })}
        />
      </dd>
    </div>
  );
}

function KeySetup({ create }: { create: ReturnType<typeof useCreateKey> }) {
  const { t } = useTranslation();
  const client = useClient();
  const { workspace } = useWorkspace();
  const keys = useQuery({
    queryKey: ["keys", workspace.id, "integration"],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/users/me/keys", {
            params: {
              query: { workspace_id: workspace.id, cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const hasKey = keys.data?.some(
    (key) =>
      !key.revoked_at &&
      (!key.expires_at || Date.parse(key.expires_at) > Date.now()),
  );
  return (
    <div className="grid gap-3">
      <div className="flex items-center justify-between gap-3">
        <h3 className="text-sm font-medium">{t("API key")}</h3>
        <Button
          type="button"
          variant="ghost"
          size="sm"
          disabled={create.isPending}
          onClick={() =>
            create.mutate({
              name: `auto-app-${crypto.randomUUID().slice(0, 8)}`,
              expires: "never",
            })
          }
        >
          {t(create.isPending ? "Creating…" : "Create key")}
        </Button>
      </div>
      {keys.isPending && <InlineLoading />}
      <ErrorNotice error={keys.error} retry={() => void keys.refetch()} />
      {hasKey !== undefined && (
        <p className="text-xs text-muted-foreground">
          {t(
            hasKey
              ? "Use a saved key, or create a new one."
              : "No active personal key. Create one to get started.",
          )}
        </p>
      )}
      <ErrorNotice error={create.error} />
    </div>
  );
}
