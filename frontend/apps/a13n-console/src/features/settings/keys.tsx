import { Button, ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { CopyButton, CopyableId } from "../../shared/copy";
import { PageActions } from "../../shared/page-actions";

import { KeyIcon, PlusIcon } from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  Empty,
  ErrorNotice,
  Loading,
  StateBadge,
  Timestamp,
} from "../../shared/feedback";
import { Confirm, FormActions } from "../../shared/form";
import styles from "../../shared/shared.module.css";
import {
  expirationOptions,
  expirationTimestamp,
  type Expiration,
} from "./expiration";

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
          .GET("/api/v1/workspaces/{workspace}/api-keys", {
            signal,
            params: { path: { workspace: workspace.id }, query },
          })
          .then(data);
      return client.http
        .GET("/api/v1/workspaces/{workspace}/personal-api-keys", {
          signal,
          params: { path: { workspace: workspace.id }, query },
        })
        .then(data);
    },
  });
  return (
    <div className={styles.stack}>
      <PageActions>
        {!memberKeys && (!accountId || can("api_key.manage")) && (
          <CreateKey accountId={accountId} />
        )}
      </PageActions>
      {query.isPending ? (
        <Loading />
      ) : query.error ? (
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("Name"),
                render: (item) => (
                  <>
                    <KeyIcon size={13} /> {item.name}
                    <small>
                      <CopyableId value={item.id} />
                    </small>
                  </>
                ),
              },
              ...(memberKeys
                ? [
                    {
                      label: t("Owner"),
                      render: (
                        item: NonNullable<typeof query.data>["items"][number],
                      ) => <CopyableId value={item.principal_id} />,
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
                align: "right",
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
    [expires, setExpires] = useState<Expiration>("never");
  const create = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      const body = {
        name,
        expires_at: expirationTimestamp(expires),
      };
      return accountId
        ? client.http
            .POST("/api/v1/service-accounts/{account_id}/api-keys", {
              params: { path: { account_id: accountId } },
              body,
            })
            .then(data)
        : client.http
            .POST("/api/v1/workspaces/{workspace}/personal-api-keys", {
              params: { path: { workspace: workspace.id } },
              body,
            })
            .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({ queryKey: ["keys", workspace.id] });
    },
  });
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (!create.isPending) {
          setOpen(value);
          create.reset();
          setName("");
          setExpires("never");
        }
      }}
      trigger={
        <Button variant="default" type="button">
          {<PlusIcon size={14} />}
          {t("Create key")}
        </Button>
      }
      size={"md"}
      title={t("Create API key")}
      description={t(
        "The key belongs to this workspace and is shown only once.",
      )}
      closeLabel={t("Close")}
      open={open}
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
          <FormField className="min-w-0 w-full" label={t("Name")}>
            <Input
              required={true}
              value={name}
              onChange={(event) => setName(event.target.value)}
              maxLength={128}
              autoFocus
            />
          </FormField>
          <ChoiceField
            placeholder={t("Select expiration")}
            value={expires}
            className="min-w-0"
            onValueChange={(value) => {
              const option = expirationOptions.find(
                (option) => option.value === value,
              );
              if (option) setExpires(option.value);
            }}
            label={t("Expires after")}
            options={expirationOptions.map((option) => ({
              ...option,
              label: t(option.label),
            }))}
          />
          <ErrorNotice error={create.error} />
          <FormActions pending={create.isPending} label={t("Create key")} />
        </form>
      )}
    </ModalFrame>
  );
}
export function SecretReveal({ value }: { value: string }) {
  const { t } = useTranslation();
  return (
    <div className={styles.stack}>
      <p>{t("Copy this value now. It will not be displayed again.")}</p>
      <FormField className="min-w-0 w-full" label={t("One-time value")}>
        <Input value={value} readOnly autoComplete="off" />
      </FormField>
      <CopyButton value={value} />
    </div>
  );
}
