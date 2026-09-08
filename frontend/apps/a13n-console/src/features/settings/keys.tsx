import { useState } from "react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, Dialog, Input } from "a13n-ui";
import { Copy, KeyRound, Plus } from "lucide-react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import {
  Empty,
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Confirm, FormActions } from "../../shared/form";
import { Pagination, Table, useCursor } from "../../shared/collection";
import styles from "../../shared/shared.module.css";

export function ApiKeys({
  accountId,
  memberKeys = false,
}: {
  accountId?: string;
  memberKeys?: boolean;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace, can } = useWorkspace(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["keys", workspace.id, accountId, memberKeys, page.cursor],
    queryFn: ({ signal }) => {
      const query = { cursor: page.cursor, limit: 30 };
      if (accountId)
        return client.http
          .GET("/api/v1/service-accounts/{account_id}/api-keys", {
            signal,
            params: { path: { account_id: accountId }, query },
          })
          .then(data);
      if (memberKeys)
        return client.http
          .GET("/api/v1/workspaces/{workspace_id}/api-keys", {
            signal,
            params: { path: { workspace_id: workspace.id }, query },
          })
          .then(data);
      return client.http
        .GET("/api/v1/workspaces/{workspace_id}/personal-api-keys", {
          signal,
          params: { path: { workspace_id: workspace.id }, query },
        })
        .then(data);
    },
  });
  return (
    <div className={styles.stack}>
      <div className={styles.toolbar}>
        <p className={styles.muted}>
          {t(
            memberKeys
              ? "Inspect and revoke member keys. Bearer values cannot be recovered."
              : "These keys only grant access within this workspace.",
          )}
        </p>
        {!memberKeys && (!accountId || can("api_key.manage")) && (
          <CreateKey accountId={accountId} />
        )}
      </div>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : query.data?.items.length ? (
        <>
          <Table
            items={query.data.items}
            columns={[
              {
                label: t("Name"),
                render: (item) => (
                  <>
                    <KeyRound size={13} /> {item.name}
                    <small>{item.id}</small>
                  </>
                ),
              },
              ...(memberKeys
                ? [
                    {
                      label: t("Owner"),
                      render: (
                        item: NonNullable<typeof query.data>["items"][number],
                      ) => <code>{item.principal_id}</code>,
                    },
                  ]
                : []),
              {
                label: t("Created"),
                render: (item) => <Timestamp value={item.created_at} />,
              },
              {
                label: t("Expires"),
                render: (item) =>
                  item.expires_at ? (
                    <Timestamp value={item.expires_at} />
                  ) : (
                    t("No expiration")
                  ),
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StateBadge
                    state={
                      item.revoked_at
                        ? "revoked"
                        : item.expires_at &&
                            new Date(item.expires_at).getTime() < Date.now()
                          ? "expired"
                          : "active"
                    }
                  />
                ),
              },
              {
                label: t("Actions"),
                render: (item) =>
                  !item.revoked_at && (
                    <Confirm
                      title={t("Revoke API key")}
                      description={t(
                        "Applications using this key will lose access immediately.",
                      )}
                      danger
                      trigger={t("Revoke")}
                      action={() =>
                        client.http.POST("/api/v1/api-keys/{key_id}/revoke", {
                          params: { path: { key_id: item.id } },
                        })
                      }
                    />
                  ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        <Empty
          title={t("No API keys")}
          description={t(
            "Create a key when you need to connect an application.",
          )}
        />
      )}
    </div>
  );
}
function CreateKey({ accountId }: { accountId?: string }) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const [open, setOpen] = useState(false),
    [name, setName] = useState(""),
    [expires, setExpires] = useState("");
  const create = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const body = {
        name,
        expires_at: expires ? new Date(expires).toISOString() : null,
      };
      return accountId
        ? client.http
            .POST("/api/v1/service-accounts/{account_id}/api-keys", {
              params: { path: { account_id: accountId } },
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace_id}/personal-api-keys", {
              params: { path: { workspace_id: workspace.id } },
              body,
            })
            .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["keys", workspace.id] });
    },
  });
  return (
    <Dialog
      title={t("Create API key")}
      description={t(
        "The key belongs to this workspace and is shown only once.",
      )}
      closeLabel={t("Close")}
      open={open}
      onOpenChange={(value) => {
        if (!create.isPending) {
          setOpen(value);
          create.reset();
          setName("");
          setExpires("");
        }
      }}
      trigger={
        <Button icon={<Plus size={14} />} variant="primary">
          {t("Create key")}
        </Button>
      }
    >
      {create.data ? (
        <SecretReveal value={create.data.bearer} />
      ) : (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            create.mutate();
          }}
        >
          <Input
            label={t("Name")}
            value={name}
            onChange={(event) => setName(event.target.value)}
            required
            maxLength={128}
            autoFocus
          />
          <Input
            label={t("Expiration date")}
            type="datetime-local"
            value={expires}
            onChange={(event) => setExpires(event.target.value)}
            hint={t("Leave empty for no expiration.")}
          />
          <ErrorNotice error={create.error} />
          <FormActions pending={create.isPending} label={t("Create key")} />
        </form>
      )}
    </Dialog>
  );
}
export function SecretReveal({ value }: { value: string }) {
  const { t } = useTranslation();
  const copy = useMutation({
    mutationFn: () => navigator.clipboard.writeText(value),
  });
  return (
    <div className={styles.stack}>
      <p>{t("Copy this value now. It will not be displayed again.")}</p>
      <Input
        label={t("One-time value")}
        value={value}
        readOnly
        autoComplete="off"
      />
      <Button icon={<Copy size={14} />} onClick={() => copy.mutate()}>
        {t(copy.isSuccess ? "Copied" : "Copy")}
      </Button>
      <ErrorNotice error={copy.error} />
    </div>
  );
}
