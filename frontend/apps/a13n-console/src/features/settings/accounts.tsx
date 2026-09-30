import {
  Button,
  ChoiceField,
  FormField,
  Input,
  Menu,
  MenuItem,
  MenuPopup,
  MenuTrigger,
  ModalFrame,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";
import { Link, useNavigate } from "react-router";
import { PageActions } from "../../shared/page";

import {
  ArrowLeftIcon,
  DotsThreeOutlineVerticalIcon,
  PencilSimpleIcon,
  PlusIcon,
  RobotIcon,
  TrashIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { Confirm } from "../../shared/dialogs";
import { CopyableId, IconTile } from "../../shared/identity";
import { FormActions } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import settings from "./settings.module.css";
import { ApiKeys } from "./keys";

function accountsPath(basePath: string) {
  return `${basePath}/settings/service-accounts`;
}

export function ServiceAccounts() {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace, basePath } = useWorkspace(),
    cache = useQueryClient(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["service-accounts", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace_id}/service-accounts", {
          signal,
          params: {
            path: { workspace_id: workspace.id },
            query: { cursor: page.cursor, limit: 30 },
          },
        })
        .then(data),
  });
  const items = query.data?.items ?? [];
  const create = <AccountEditor />;
  return (
    <div className={styles.stack}>
      <PageActions>{create}</PageActions>
      {query.isPending ? (
        <Loading variant="table" columns={4} rows={5} />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : items.length ? (
        <>
          <ResourceTable
            items={items}
            caption={t("Service accounts")}
            rowMenuLabel={t("Account actions")}
            rowMenu={(item) => (
              <>
                <AccountEditor
                  account={item}
                  triggerElement={
                    <MenuItem closeOnClick={false}>
                      <PencilSimpleIcon size={14} />
                      {t("Edit")}
                    </MenuItem>
                  }
                />
                <Confirm
                  subject={item.name}
                  title={t("Delete service account")}
                  description={t(
                    "This revokes its keys and removes workspace access.",
                  )}
                  triggerElement={
                    <MenuItem closeOnClick={false} variant="destructive">
                      <TrashIcon size={14} />
                      {t("Delete")}
                    </MenuItem>
                  }
                  danger
                  action={async () => {
                    await client.http.DELETE(
                      "/api/v1/workspaces/{workspace_id}/service-accounts/{account_id}",
                      {
                        params: {
                          path: {
                            workspace_id: workspace.id,
                            account_id: item.id,
                          },
                        },
                        headers: ifMatch(rowTag(item)),
                      },
                    );
                    await cache.invalidateQueries({
                      queryKey: ["service-accounts", workspace.id],
                    });
                  }}
                />
              </>
            )}
            columns={[
              {
                label: t("Account"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    icon={<RobotIcon size={15} aria-hidden="true" />}
                    name={item.name}
                    description={item.description}
                    to={`${accountsPath(basePath)}/${item.id}`}
                    resourceId={item.id}
                  />
                ),
              },
              {
                label: t("Role"),
                render: (item) =>
                  item.role && (
                    <span className={settings.chip}>
                      {t(`role.${item.role}`, { defaultValue: item.role })}
                    </span>
                  ),
              },
              {
                label: t("Status"),
                render: (item) => <StatePill state={item.status} />,
              },
              {
                label: t("Created"),
                tone: "muted",
                render: (item) => (
                  <Timestamp value={item.created_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} service accounts", { count: items.length })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        <Empty
          icon={<RobotIcon aria-hidden="true" />}
          title={t("No service accounts")}
          description={t("Create a dedicated identity for each application.")}
          action={create}
        />
      )}
    </div>
  );
}

/** One account and the keys it holds, addressed by its own URL. */
export function ServiceAccountDetail({ accountId }: { accountId: string }) {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace, basePath } = useWorkspace(),
    cache = useQueryClient(),
    navigate = useNavigate();
  const query = useQuery({
    queryKey: ["service-account", accountId],
    queryFn: ({ signal }) =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace_id}/service-accounts/{account_id}",
          {
            signal,
            params: {
              path: { workspace_id: workspace.id, account_id: accountId },
            },
          },
        )
        .then(data),
  });
  const list = accountsPath(basePath);
  if (query.isPending) return <Loading variant="detail" />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  const account = query.data;
  return (
    <div className={settings.sections}>
      <div className={styles.stack}>
        <Link className={settings.contentBack} to={list}>
          <ArrowLeftIcon size={14} />
          {t("Service accounts")}
        </Link>
        <div className={settings.detailHeader}>
          <IconTile size={36}>
            <RobotIcon size={18} aria-hidden="true" />
          </IconTile>
          <span className={settings.detailIdentity}>
            <strong>{account.name}</strong>
            {account.description && <span>{account.description}</span>}
          </span>
          <div className={settings.detailActions}>
            <StatePill state={account.status} />
            <AccountEditor account={account} />
            <Menu>
              <MenuTrigger
                render={
                  <Button
                    variant="ghost"
                    size="icon-sm"
                    type="button"
                    aria-label={t("Account actions")}
                    title={t("Account actions")}
                  />
                }
              >
                <DotsThreeOutlineVerticalIcon size={14} weight="fill" />
              </MenuTrigger>
              <MenuPopup align="end">
                <Confirm
                  subject={account.name}
                  title={t("Delete service account")}
                  description={t(
                    "This revokes its keys and removes workspace access.",
                  )}
                  triggerElement={
                    <MenuItem closeOnClick={false} variant="destructive">
                      <TrashIcon size={14} />
                      {t("Delete")}
                    </MenuItem>
                  }
                  danger
                  onSuccess={() => {
                    void cache.invalidateQueries({
                      queryKey: ["service-accounts", workspace.id],
                    });
                    void navigate(list, { replace: true });
                  }}
                  action={() =>
                    client.http.DELETE(
                      "/api/v1/workspaces/{workspace_id}/service-accounts/{account_id}",
                      {
                        params: {
                          path: {
                            workspace_id: workspace.id,
                            account_id: account.id,
                          },
                        },
                        headers: ifMatch(rowTag(account)),
                      },
                    )
                  }
                />
              </MenuPopup>
            </Menu>
          </div>
        </div>
        <dl className={settings.facts}>
          <div>
            <dt>{t("Role")}</dt>
            <dd>
              {account.role &&
                t(`role.${account.role}`, { defaultValue: account.role })}
            </dd>
          </div>
          <div>
            <dt>{t("Created")}</dt>
            <dd>
              <Timestamp value={account.created_at} />
            </dd>
          </div>
          <div>
            <dt>{t("ID")}</dt>
            <dd>
              <CopyableId value={account.id} />
            </dd>
          </div>
        </dl>
      </div>
      <ApiKeys accountId={account.id} actions="inline" />
    </div>
  );
}

function AccountEditor({
  account,
  triggerElement,
}: {
  account?: Schema["ServiceAccount"];
  triggerElement?: ReactElement;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace } = useWorkspace(),
    cache = useQueryClient();
  const [basis, setBasis] = useState(account),
    [open, setOpen] = useState(false),
    [name, setName] = useState(account?.name ?? ""),
    [role, setRole] = useState<"viewer" | "runner" | "builder">(
      account?.role === "runner" || account?.role === "builder"
        ? account.role
        : "viewer",
    ),
    [status, setStatus] = useState<"active" | "disabled">(
      account?.status === "disabled" ? "disabled" : "active",
    );
  function load(value: Schema["ServiceAccount"] | undefined) {
    setBasis(value);
    setName(value?.name ?? "");
    setRole(
      value?.role === "runner" || value?.role === "builder"
        ? value.role
        : "viewer",
    );
    setStatus(value?.status === "disabled" ? "disabled" : "active");
  }
  const reload = useMutation({
    mutationFn: () =>
      client.http
        .GET(
          "/api/v1/workspaces/{workspace_id}/service-accounts/{account_id}",
          {
            params: {
              path: { workspace_id: workspace.id, account_id: account!.id },
            },
          },
        )
        .then(data),
    onSuccess: (value) => {
      load(value);
      mutation.reset();
    },
  });
  const mutation = useMutation({
    mutationFn: () =>
      basis
        ? client.http.PATCH(
            "/api/v1/workspaces/{workspace_id}/service-accounts/{account_id}",
            {
              params: {
                path: { workspace_id: workspace.id, account_id: basis.id },
              },
              headers: ifMatch(rowTag(basis)),
              body: { name, role, status },
            },
          )
        : client.http.POST(
            "/api/v1/workspaces/{workspace_id}/service-accounts",
            {
              params: { path: { workspace_id: workspace.id } },
              body: { name, role },
            },
          ),
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: ["service-accounts", workspace.id],
      });
      if (account)
        void cache.invalidateQueries({
          queryKey: ["service-account", account.id],
        });
      setOpen(false);
    },
  });
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (!mutation.isPending) {
          if (value) load(account);
          setOpen(value);
          mutation.reset();
        }
      }}
      trigger={
        triggerElement ?? (
          <Button
            variant={account ? "outline" : "default"}
            type="button"
            size={account ? "sm" : undefined}
          >
            {!account && <PlusIcon size={14} />}
            {account ? t("Edit") : t("Create account")}
          </Button>
        )
      }
      size="md"
      title={t(account ? "Edit service account" : "Create service account")}
      description={t("Select the minimum role this application needs.")}
      closeLabel={t("Close")}
      open={open}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          mutation.mutate();
        }}
      >
        <FormField className="min-w-0 w-full" label={t("Name")}>
          <Input
            required={true}
            value={name}
            onChange={(event) => setName(event.target.value)}
          />
        </FormField>
        <ChoiceField
          placeholder={t("Select…")}
          value={role}
          className="min-w-0"
          onValueChange={(value) => {
            if (value === "viewer" || value === "runner" || value === "builder")
              setRole(value);
          }}
          label={t("Role")}
          options={["viewer", "runner", "builder"].map((value) => ({
            value,
            label: t(`role.${value}`, { defaultValue: value }),
          }))}
        />
        {account && (
          <ChoiceField
            placeholder={t("Select…")}
            value={status}
            className="min-w-0"
            onValueChange={(value) => {
              if (value === "active" || value === "disabled") setStatus(value);
            }}
            label={t("Status")}
            options={[
              { value: "active", label: t("Active") },
              { value: "disabled", label: t("Disabled") },
            ]}
          />
        )}
        <ErrorNotice
          error={mutation.error ?? reload.error}
          retry={account ? () => reload.mutate() : undefined}
        />
        <FormActions
          pending={mutation.isPending}
          onCancel={() => setOpen(false)}
          label={t(account ? "Save changes" : "Create service account")}
        />
      </form>
    </ModalFrame>
  );
}
