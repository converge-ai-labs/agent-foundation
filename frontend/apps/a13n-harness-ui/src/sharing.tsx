import { useState } from "react";
import { useLocation, useSearchParams } from "react-router";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, ChoiceField, Input, ModalFrame } from "a13n-ui";
import { Check, Copy, LinkSimple, ShareNetwork } from "@phosphor-icons/react";
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
      title="Share access"
      description="Invite someone into this conversation or Project with a temporary link."
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
    onSuccess: setCreated,
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
      {created ? (
        <section className={styles.result}>
          <div className={styles.resultHeading}>
            <Check size={20} />
            <div>
              <h3>Your link is ready</h3>
              <p>
                {created.grant.scope === "thread"
                  ? "This conversation"
                  : `Entire Project · ${projectName}`}
              </p>
              <p>
                {currentGrant?.permission === "view"
                  ? "Can view"
                  : "Can prompt"}{" "}
                · Expires {expires(created.grant.expires_at)}
              </p>
            </div>
          </div>
          <Input
            aria-label="Sharing link"
            readOnly
            value={link}
            onFocus={(e) => e.target.select()}
          />
          <div className={styles.actions}>
            <Button onClick={() => void copy()}>
              <Copy size={16} />
              {copied ? "Copied" : "Copy link"}
            </Button>
            <a href={link} target="_blank" rel="noreferrer">
              Preview as guest
            </a>
          </div>
          <p className={styles.hint}>
            Anyone with this link can use it. Copy it before closing; the key is
            shown only once.
          </p>
          {copyError && <p role="status">{copyError}</p>}
          <Button
            variant="ghost"
            size="sm"
            onClick={() => {
              setCreated(null);
              setCopied(false);
              create.reset();
            }}
          >
            Create another link
          </Button>
        </section>
      ) : (
        <>
          <ChoiceField
            label="Share"
            value={scope}
            options={[
              ...(threadId
                ? [{ value: "thread", label: "This conversation" }]
                : []),
              ...(projectId
                ? [
                    {
                      value: "project",
                      label: `Entire Project · ${projectName}`,
                    },
                  ]
                : []),
            ]}
            onValueChange={(value) => setScope(value as Scope)}
          />
          {scope === "project" && (
            <p className={styles.hint}>
              Includes existing and future conversations in {projectName}.
            </p>
          )}
          <div
            className={styles.permissionChoices}
            role="group"
            aria-label="Link permission"
          >
            {permissions.map((item) => (
              <button
                key={item.value}
                type="button"
                aria-pressed={permission === item.value}
                className={
                  permission === item.value
                    ? styles.selectedPermission
                    : styles.permission
                }
                onClick={() => setPermission(item.value as Permission)}
              >
                <strong>{item.label}</strong>
                <span>
                  {item.value === "view"
                    ? "Read messages and follow live output."
                    : "Write together, send prompts and steer work."}
                </span>
              </button>
            ))}
          </div>
          {permission === "prompt" && (
            <p className={styles.authority}>
              Share with people you trust. Prompts use this Agent’s tools and
              Environment with its existing authority.
              {scope === "project"
                ? " Guests can also start conversations using Project defaults."
                : ""}
            </p>
          )}
          <ChoiceField
            label="Link expires after"
            value={String(hours)}
            options={[
              { value: "1", label: "1 hour" },
              { value: "24", label: "1 day" },
              { value: "168", label: "7 days" },
            ]}
            onValueChange={(value) => setHours(Number(value) as 1 | 24 | 168)}
          />
          <ErrorNotice error={create.error} />
          {create.isError && (
            <p className={styles.hint}>
              If the connection was interrupted, check active links below before
              creating another.
            </p>
          )}
          <div className={styles.actions}>
            <Button loading={create.isPending} onClick={() => create.mutate()}>
              <LinkSimple size={16} />
              Create link
            </Button>
            <span className={styles.hint}>
              You can change access or revoke it anytime.
            </span>
          </div>
        </>
      )}
      <details className={styles.manage}>
        <summary>
          Active links{grants.data ? ` · ${grants.data.length}` : ""}
        </summary>
        <ErrorNotice error={grants.error} retry={() => void grants.refetch()} />
        {grants.isPending && <p>Loading links…</p>}
        {grants.data?.length === 0 && (
          <p className={styles.hint}>
            No active links for this{" "}
            {scope === "thread" ? "conversation" : "Project"}.
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
