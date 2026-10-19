import { useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, Input, ModalFrame } from "a13n-ui";
import {
  ArrowUpRight,
  Check,
  Copy,
  Globe,
  LinkSimple,
  ShareNetwork,
} from "@phosphor-icons/react";
import { result, type Schema } from "./transport/client";
import { useProjects, useTransport } from "./transport/context";
import { useThread } from "./conversations/queries";
import { ErrorNotice } from "./shell/ui";
import styles from "./sharing.module.css";

type Permission = Schema<"ShareGrant">["permission"];
type Scope = Schema<"ShareGrant">["scope"];
const permissions = [
  { value: "view", label: "Can view" },
  { value: "prompt", label: "Can prompt" },
];
const expires = (value: string) =>
  new Date(value).toLocaleString(undefined, {
    month: "short",
    day: "numeric",
    hour: "numeric",
    minute: "2-digit",
  });

export function ShareButton() {
  const location = useLocation();
  const [search, setSearch] = useSearchParams();
  const threadId = /^\/threads\/([^/]+)\/?$/.exec(location.pathname)?.[1];
  const detail = useThread(threadId ?? "");
  if (
    !threadId ||
    detail.data?.thread.memory_scope ||
    detail.data?.thread.configuration.agent_source.kind === "live"
  )
    return null;
  return (
    <Button
      variant="ghost"
      size="sm"
      onClick={() =>
        setSearch(
          new URLSearchParams([...search.entries(), ["dialog", "share"]]),
        )
      }
    >
      <ShareNetwork aria-hidden size={16} />
      Share
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
  return (
    <ModalFrame
      open={open}
      onOpenChange={(value) => {
        if (!value) onClose();
      }}
      title="Share"
      closeLabel="Close"
    >
      {open && <ShareContents threadId={threadId} projectId={projectId} />}
    </ModalFrame>
  );
}

function ShareContents({
  threadId,
  projectId,
}: {
  threadId?: string;
  projectId?: string | null;
}) {
  const { client } = useTransport();
  const queries = useQueryClient();
  const projects = useProjects();
  const [scope, setScope] = useState<Scope>(threadId ? "thread" : "project");
  const [permission, setPermission] = useState<Permission>("view");
  const [hours, setHours] = useState<1 | 24 | 168>(24);
  const [created, setCreated] = useState<Schema<"ShareCreated"> | null>(null);
  const [copied, setCopied] = useState(false);
  const [copyError, setCopyError] = useState("");
  const target = (scope === "thread" ? threadId : projectId)!;
  const projectName =
    projects.data?.find((p) => p.project_id === projectId)?.name ?? "Project";
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
      setCopyError("");
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
    setCopyError("");
    try {
      await navigator.clipboard.writeText(link);
      setCopied(true);
    } catch {
      setCopyError("Select the link above and copy it manually.");
    }
  };
  return (
    <div className={styles.panel}>
      <div className={styles.scope}>
        <ChoiceField
          label="Share"
          hideLabel
          value={scope}
          readOnly={!!created || !(threadId && projectId)}
          disabled={create.isPending}
          options={[
            ...(threadId ? [{ value: "thread", label: "Thread" }] : []),
            ...(projectId
              ? [{ value: "project", label: `Project · ${projectName}` }]
              : []),
          ]}
          onValueChange={(value) => setScope(value as Scope)}
        />
        {scope === "project" && (
          <p className={styles.hint}>Includes current and future Threads.</p>
        )}
      </div>
      <section className={styles.linkSettings} aria-label="Link sharing">
        <div className={styles.permissionRow}>
          <span className={styles.linkAudience}>
            <span className={styles.linkIcon}>
              <Globe size={20} aria-hidden />
            </span>
            Anyone with the link
          </span>
          <ChoiceField
            label="Link permission"
            className={styles.permissionControl}
            hideLabel
            value={created ? currentGrant?.permission : permission}
            readOnly={!!created}
            disabled={create.isPending}
            options={permissions}
            onValueChange={(value) => setPermission(value as Permission)}
          />
        </div>
        {!created && permission === "prompt" && (
          <p className={styles.authority}>
            For trusted people: prompts use the Agent’s tools and Environment
            permissions.
            {scope === "project" ? " Guests can also create Threads." : ""}
          </p>
        )}
        {created && (
          <div className={styles.linkResult}>
            <Input
              aria-label="Sharing link"
              readOnly
              value={link}
              onFocus={(e) => e.target.select()}
            />
            <p className={styles.hint}>
              Copy before closing. This link is shown only once.
            </p>
            {copyError && (
              <p className={styles.hint} role="status">
                {copyError}
              </p>
            )}
          </div>
        )}
      </section>
      <div className={styles.actions}>
        {created ? (
          <span className={styles.hint}>
            Expires {expires(created.grant.expires_at)}
          </span>
        ) : (
          <ChoiceField
            label="Expires in"
            variant="filter"
            value={String(hours)}
            disabled={create.isPending}
            options={[
              { value: "1", label: "1 hour" },
              { value: "24", label: "1 day" },
              { value: "168", label: "7 days" },
            ]}
            onValueChange={(value) => setHours(Number(value) as 1 | 24 | 168)}
          />
        )}
        {created ? (
          <Button onClick={() => void copy()}>
            {copied ? (
              <Check size={16} aria-hidden />
            ) : (
              <Copy size={16} aria-hidden />
            )}
            <span aria-live="polite">{copied ? "Copied" : "Copy link"}</span>
          </Button>
        ) : (
          <Button loading={create.isPending} onClick={() => create.mutate()}>
            <LinkSimple size={16} aria-hidden />
            Create link
          </Button>
        )}
      </div>
      {!created && <ErrorNotice error={create.error} />}
      {!created && create.isError && (
        <p className={styles.hint}>
          Check active links before retrying; the link may have been created.
        </p>
      )}
      {created && (
        <div className={styles.secondaryActions}>
          <a href={link} target="_blank" rel="noreferrer">
            Preview as guest <ArrowUpRight size={14} aria-hidden />
          </a>
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setCreated(null);
              setCopied(false);
              setCopyError("");
              create.reset();
            }}
          >
            Create another link
          </Button>
        </div>
      )}
      <details className={styles.manage}>
        <summary>
          Active links{grants.data ? ` · ${grants.data.length}` : ""}
        </summary>
        <ErrorNotice error={grants.error} retry={() => void grants.refetch()} />
        {grants.isPending && <p>Loading links…</p>}
        {grants.data?.length === 0 && (
          <p className={styles.hint}>
            No active links for this {scope === "thread" ? "Thread" : "Project"}
            .
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
      </details>
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
        <strong>Created {expires(grant.created_at)}</strong>
        <p>Expires {expires(grant.expires_at)}</p>
      </div>
      <ChoiceField
        label="Permission"
        className={styles.permissionControl}
        hideLabel
        value={permission}
        options={permissions}
        onValueChange={(value) => setPermission(value as Permission)}
      />
      {permission !== grant.permission && (
        <Button
          variant="outline"
          size="sm"
          loading={update.isPending}
          onClick={() => update.mutate()}
        >
          Save
        </Button>
      )}
      <Button
        variant="ghost"
        size="sm"
        loading={revoke.isPending}
        onClick={() => revoke.mutate()}
      >
        Revoke
      </Button>
      {permission === "prompt" && grant.permission !== "prompt" && (
        <p className={styles.authority}>
          Prompts use this Agent’s tools and Environment with its existing
          authority. Save only for people you trust.
        </p>
      )}
      <ErrorNotice error={update.error || revoke.error} />
    </div>
  );
}
