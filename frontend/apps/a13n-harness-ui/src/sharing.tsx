import type { TFunction } from "i18next";
import { useTranslation } from "./i18n";
import { useId, useState, type ReactNode } from "react";
import { useLocation, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, Input, ModalFrame } from "a13n-ui";
import {
  ArrowUpRight,
  CaretDown,
  ChatCircle,
  Check,
  Clock,
  Copy,
  GlobeSimple,
  LinkSimple,
  ShareNetwork,
} from "@phosphor-icons/react";
import { result, type Schema } from "./transport/client";
import { useProjects, useTransport } from "./transport/context";
import { useThread } from "./conversations/queries";
import { isUnavailableChat } from "./conversations/conversation-unavailable";
import { ErrorNotice } from "./shell/ui";
import styles from "./sharing.module.css";
import { copyText } from "./platform";
import { BOT_THREAD_ID } from "./bot-identity";

type Permission = Schema<"ShareGrant">["permission"];
type Scope = Schema<"ShareGrant">["scope"];
const permissions = (t: TFunction) => [
  { value: "view", label: t("View only") },
  { value: "prompt", label: t("Send messages") },
];
const expires = (value: string, locale: string | undefined) =>
  new Date(value).toLocaleString(locale, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });

export function ShareButton() {
  const { t } = useTranslation();
  const location = useLocation();
  const [search, setSearch] = useSearchParams();
  const threadId = /^\/threads\/([^/]+)\/?$/.exec(location.pathname)?.[1];
  const detail = useThread(threadId ?? "");
  if (
    !threadId ||
    threadId === BOT_THREAD_ID ||
    isUnavailableChat(detail.error) ||
    detail.data?.thread.memory_scope ||
    detail.data?.thread.configuration.agent_source.kind === "live"
  )
    return null;
  return (
    <Button
      variant="ghost"
      size="icon-sm"
      aria-label={t("Share")}
      title={t("Share")}
      onClick={() =>
        setSearch(
          new URLSearchParams([...search.entries(), ["dialog", "share"]]),
        )
      }
    >
      <ShareNetwork aria-hidden size={16} />
    </Button>
  );
}

export function SharePanel({
  open,
  onClose,
  threadId,
  projectId,
}: {
  open: boolean;
  onClose: () => void;
  threadId?: string;
  projectId?: string | null;
}) {
  const { t } = useTranslation();
  return (
    <ModalFrame
      bottomStickOnMobile
      open={open}
      onOpenChange={(value) => {
        if (!value) onClose();
      }}
      title={t("Share")}
      closeLabel={t("Close")}
    >
      {open && <ShareContents threadId={threadId} projectId={projectId} />}
    </ModalFrame>
  );
}

function LinkOption({
  icon,
  label,
  description,
  children,
}: {
  icon: ReactNode;
  label: string;
  description?: ReactNode;
  children: ReactNode;
}) {
  return (
    <div className={styles.linkOption}>
      {icon}
      <span>{label}</span>
      {children}
      {description && <p className={styles.optionHint}>{description}</p>}
    </div>
  );
}

function ShareContents({
  threadId,
  projectId,
}: {
  threadId?: string;
  projectId?: string | null;
}) {
  const { t, i18n } = useTranslation();
  const { client } = useTransport();
  const queries = useQueryClient();
  const projects = useProjects();
  const [scope, setScope] = useState<Scope>(threadId ? "thread" : "project");
  const [permission, setPermission] = useState<Permission>("view");
  const [hours, setHours] = useState<1 | 24 | 168>(24);
  const [created, setCreated] = useState<Schema<"ShareCreated"> | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState(false);
  const [linksOpen, setLinksOpen] = useState(false);
  const linksId = useId();
  const target = (scope === "thread" ? threadId : projectId)!;
  const projectName =
    projects.data?.find((p) => p.project_id === projectId)?.name ??
    t("Project");
  const grants = useQuery({
    queryKey: ["shares", scope, target],
    queryFn: ({ signal }) =>
      result(
        client.GET("/api/shares", {
          params: { query: { scope, target_id: target } },
          signal,
        }),
      ),
  });
  const refresh = () =>
    void queries.invalidateQueries({ queryKey: ["shares", scope, target] });
  const create = useMutation({
    mutationFn: () =>
      result(
        client.POST("/api/shares", {
          body: { scope, target_id: target, permission, lifetime_hours: hours },
        }),
      ),
    onSuccess: (value) => {
      setCreated(value);
      setCopied(false);
      setCopyError(false);
    },
    onSettled: refresh,
    retry: false,
  });
  const currentGrant =
    grants.data?.find((grant) => grant.share_id === created?.grant.share_id) ??
    created?.grant;
  const link = created
    ? `${window.location.origin}/share/${created.grant.share_id}#key=${encodeURIComponent(created.key)}`
    : "";
  const copy = async () => {
    setCopyError(false);
    try {
      await copyText(link);
      setCopied(true);
    } catch {
      setCopyError(true);
    }
  };
  return (
    <div className={styles.panel}>
      <section className={styles.linkOptions} aria-label={t("Link sharing")}>
        <LinkOption
          icon={<ChatCircle aria-hidden />}
          label={t("Include")}
          description={
            scope === "project"
              ? t("Shares every chat in this project, including new ones.")
              : undefined
          }
        >
          <ChoiceField
            label={t("Include")}
            className={styles.linkChoice}
            hideLabel
            value={scope}
            readOnly={!!created || !(threadId && projectId)}
            disabled={create.isPending}
            options={[
              ...(threadId ? [{ value: "thread", label: t("This chat") }] : []),
              ...(projectId
                ? [
                    {
                      value: "project",
                      label: t("All chats in {{name}}", { name: projectName }),
                    },
                  ]
                : []),
            ]}
            onValueChange={(value) => setScope(value as Scope)}
          />
        </LinkOption>
        <LinkOption
          icon={<GlobeSimple aria-hidden />}
          label={t("Anyone with the link")}
          description={
            !created && permission === "prompt"
              ? scope === "project"
                ? t(
                    "People can send messages using your agent’s tools and access. Share only with people you trust. They can also start new chats.",
                  )
                : t(
                    "People can send messages using your agent’s tools and access. Share only with people you trust.",
                  )
              : undefined
          }
        >
          <ChoiceField
            label={t("Link access")}
            className={styles.linkChoice}
            hideLabel
            value={created ? currentGrant?.permission : permission}
            readOnly={!!created}
            disabled={create.isPending}
            options={permissions(t)}
            onValueChange={(value) => setPermission(value as Permission)}
          />
        </LinkOption>
        <LinkOption
          icon={<Clock aria-hidden />}
          label={created ? t("Expires") : t("Expires after")}
        >
          {created ? (
            <span className={styles.expiration}>
              {expires(created.grant.expires_at, i18n.resolvedLanguage)}
            </span>
          ) : (
            <ChoiceField
              label={t("Expires after")}
              className={styles.linkChoice}
              hideLabel
              value={String(hours)}
              disabled={create.isPending}
              options={[
                { value: "1", label: t("1 hour") },
                { value: "24", label: t("1 day") },
                { value: "168", label: t("7 days") },
              ]}
              onValueChange={(value) => setHours(Number(value) as 1 | 24 | 168)}
            />
          )}
        </LinkOption>
      </section>
      {created && (
        <div className={styles.linkResult}>
          <Input
            aria-label={t("Sharing link")}
            readOnly
            value={link}
            onFocus={(e) => e.target.select()}
          />
          <p className={styles.hint}>
            {t(
              "Copy this link before closing. You won’t be able to see it again.",
            )}
          </p>
          {copyError && (
            <p className={styles.hint} role="status">
              {t("Select the link above and copy it manually.")}
            </p>
          )}
        </div>
      )}
      <div className={styles.linkFooter}>
        <Button
          variant="ghost"
          size="sm"
          className={styles.manageButton}
          aria-expanded={linksOpen}
          aria-controls={linksId}
          onClick={() => setLinksOpen(!linksOpen)}
        >
          {grants.data
            ? t("Manage links - {{count, number}}", {
                count: grants.data.length,
              })
            : t("Manage links")}
          <CaretDown aria-hidden size={12} />
        </Button>
        {created ? (
          <Button size="sm" onClick={() => void copy()}>
            {copied ? (
              <Check size={16} aria-hidden />
            ) : (
              <Copy size={16} aria-hidden />
            )}
            <span aria-live="polite">
              {copied ? t("Copied") : t("Copy link")}
            </span>
          </Button>
        ) : (
          <Button
            size="sm"
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            <LinkSimple size={16} aria-hidden />
            {t("Create link")}
          </Button>
        )}
      </div>
      {!created && <ErrorNotice error={create.error} />}
      {!created && create.isError && (
        <p className={styles.hint}>
          {t(
            "The link may already exist. Check Manage links before trying again.",
          )}
        </p>
      )}
      {created && (
        <div className={styles.secondaryActions}>
          <a
            className={styles.textAction}
            href={link}
            target="_blank"
            rel="noreferrer"
          >
            {t("Open shared view")} <ArrowUpRight size={14} aria-hidden />
          </a>
          <button
            type="button"
            className={styles.textAction}
            onClick={() => {
              setCreated(null);
              setCopied(false);
              setCopyError(false);
              create.reset();
            }}
          >
            {t("New link")}
          </button>
        </div>
      )}
      <div id={linksId} className={styles.manage} hidden={!linksOpen}>
        <ErrorNotice error={grants.error} retry={() => void grants.refetch()} />
        {grants.isPending && <p>{t("Loading links…")}</p>}
        {grants.data?.length === 0 && (
          <p className={styles.hint}>
            {scope === "thread"
              ? t("No shared links for this chat yet.")
              : t("No shared links for this project yet.")}
          </p>
        )}
        {grants.data?.map((grant) => (
          <GrantRow
            key={`${grant.share_id}:${grant.permission}`}
            grant={grant}
            refresh={refresh}
            onRevoked={() => {
              if (created?.grant.share_id === grant.share_id) setCreated(null);
            }}
          />
        ))}
      </div>
    </div>
  );
}

function GrantRow({
  grant,
  refresh,
  onRevoked,
}: {
  grant: Schema<"ShareGrant">;
  refresh: () => void;
  onRevoked: () => void;
}) {
  const { t, i18n } = useTranslation();
  const { client } = useTransport();
  const [permission, setPermission] = useState<Permission>(grant.permission);
  const update = useMutation({
    mutationFn: () =>
      result(
        client.PATCH("/api/shares/{share_id}", {
          params: { path: { share_id: grant.share_id } },
          body: { permission },
        }),
      ),
    onSettled: refresh,
  });
  const revoke = useMutation({
    mutationFn: () =>
      client.DELETE("/api/shares/{share_id}", {
        params: { path: { share_id: grant.share_id } },
      }),
    onSuccess: onRevoked,
    onSettled: refresh,
  });
  return (
    <div className={styles.grant}>
      <div>
        <strong>
          {t("Created {{date}}", {
            date: expires(grant.created_at, i18n.resolvedLanguage),
          })}
        </strong>
        <p>
          {t("Expires {{date}}", {
            date: expires(grant.expires_at, i18n.resolvedLanguage),
          })}
        </p>
      </div>
      <ChoiceField
        label={t("Permission")}
        className={styles.permissionControl}
        hideLabel
        value={permission}
        options={permissions(t)}
        onValueChange={(value) => setPermission(value as Permission)}
      />
      {permission !== grant.permission && (
        <Button
          variant="outline"
          size="sm"
          loading={update.isPending}
          onClick={() => update.mutate()}
        >
          {t("Save")}
        </Button>
      )}
      <Button
        variant="ghost"
        size="sm"
        loading={revoke.isPending}
        onClick={() => revoke.mutate()}
      >
        {t("Revoke")}
      </Button>
      {permission === "prompt" && grant.permission !== "prompt" && (
        <p className={styles.authority}>
          {t(
            "Messages can use this agent's tools, environment and permissions. Share only with people you trust.",
          )}
        </p>
      )}
      <ErrorNotice error={update.error || revoke.error} />
    </div>
  );
}
