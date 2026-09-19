import {
  Button,
  ChoiceField,
  FormField,
  MenuItem,
  ModalFrame,
  SearchPicker,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";
import { PageActions } from "../../shared/page";

import { ApiError } from "../../service-client";
import {
  PlusIcon,
  UserMinusIcon,
  UsersIcon,
  UserSwitchIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { UserAvatar } from "../../layout/avatar";
import { useAccess } from "../../layout/workspace";
import { allPages, data, representation, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceIdentity,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { ConflictNotice, Confirm } from "../../shared/dialogs";
import { FormActions } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import settings from "./settings.module.css";
import { InvitationEditor } from "./invitations";
import { roleOptions, roles, type MembershipScope, type Role } from "./roles";

export type { MembershipScope };
export { roleOptions };

export function Members({ scope }: { scope: MembershipScope }) {
  const { t } = useTranslation(),
    client = useClient(),
    { organization, organizationAdmin, can } = useAccess(),
    page = useCursor();
  const members = useQuery({
    queryKey: ["members", scope.kind, scope.id],
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        scope.kind === "organization"
          ? client.http
              .GET("/api/v1/organizations/{organization}/users", {
                params: {
                  path: { organization: scope.id },
                  query: { cursor, limit: 100 },
                },
                signal,
              })
              .then(data)
          : client.http
              .GET("/api/v1/workspaces/{workspace}/members", {
                params: {
                  path: { workspace: scope.id },
                  query: { cursor, limit: 100 },
                },
                signal,
              })
              .then(data),
      ),
  });
  const bindings = useQuery({
    queryKey: ["bindings", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      scope.kind === "organization"
        ? client.http
            .GET("/api/v1/organizations/{organization}/role-bindings", {
              params: {
                path: { organization: scope.id },
                query: { cursor: page.cursor, limit: 30 },
              },
              signal,
            })
            .then(data)
        : client.http
            .GET("/api/v1/workspaces/{workspace}/role-bindings", {
              params: {
                path: { workspace: scope.id },
                query: { cursor: page.cursor, limit: 30 },
              },
              signal,
            })
            .then(data),
  });
  /* A role binding is only safe to change from the version the reader saw. */
  const readVersion = async (item: Schema["RoleBinding"]) => {
    const latest = representation(
      await client.http.GET("/api/v1/role-bindings/{binding_id}", {
        params: { path: { binding_id: item.id } },
      }),
    );
    if (latest.value.updated_at !== item.updated_at || !latest.etag)
      throw new ApiError(
        412,
        "precondition_failed",
        t("The member's role changed. Reload before continuing."),
        {},
        null,
      );
    return latest.etag;
  };
  /* A workspace also grants roles to service accounts; name those too. */
  const accounts = useQuery({
    queryKey: ["service-account-directory", scope.id],
    enabled: scope.kind === "workspace" && can("service_account.manage"),
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/workspaces/{workspace}/service-accounts", {
            params: {
              path: { workspace: scope.id },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const person = (item: Schema["RoleBinding"]) => {
    const user = members.data?.find((user) => user.id === item.principal_id);
    if (user)
      return { name: user.name, secondary: user.email, image: user.image_url };
    const account = accounts.data?.find(
      (account) => account.id === item.principal_id,
    );
    if (account) return { name: account.name, secondary: t("Service account") };
    return { name: item.principal_id, secondary: item.principal_type };
  };
  const items = bindings.data?.items ?? [];
  const action =
    scope.kind === "workspace" ? (
      organizationAdmin && (
        <AddMember scope={scope} organizationId={organization.id} />
      )
    ) : (
      <InvitationEditor scope={scope} />
    );
  return (
    <div className={styles.stack}>
      <PageActions>{action}</PageActions>
      {members.isPending || bindings.isPending ? (
        <Loading variant="table" columns={3} rows={5} />
      ) : members.error || bindings.error ? (
        <ErrorNotice error={members.error ?? bindings.error} />
      ) : items.length ? (
        <>
          <ResourceTable
            items={items}
            caption={t("Members")}
            rowMenuLabel={t("Member actions")}
            rowMenu={(item) => (
              <>
                <ChangeRole
                  item={item}
                  scope={scope}
                  readVersion={readVersion}
                  triggerElement={
                    <MenuItem closeOnClick={false}>
                      <UserSwitchIcon size={14} />
                      {t("Change role")}
                    </MenuItem>
                  }
                />
                <Confirm
                  subject={`${person(item).name} · ${t(
                    `role.${item.role_key}`,
                    {
                      defaultValue: item.role_key,
                    },
                  )}`}
                  title={t("Remove member")}
                  description={t(
                    "This removes the selected role. Other explicit grants may still allow access.",
                  )}
                  triggerElement={
                    <MenuItem closeOnClick={false} variant="destructive">
                      <UserMinusIcon size={14} />
                      {t("Remove")}
                    </MenuItem>
                  }
                  danger
                  action={async () => {
                    const etag = await readVersion(item);
                    await client.http.DELETE(
                      "/api/v1/role-bindings/{binding_id}",
                      {
                        params: {
                          path: { binding_id: item.id },
                          header: { "If-Match": etag },
                        },
                      },
                    );
                  }}
                />
              </>
            )}
            columns={[
              {
                label: t("Member"),
                tone: "primary",
                render: (item) => {
                  const identity = person(item);
                  return (
                    <ResourceIdentity
                      icon={
                        <UserAvatar
                          name={identity.name}
                          id={item.principal_id}
                          url={identity.image}
                          className="size-8 rounded-[8px]"
                        />
                      }
                      name={identity.name}
                      description={identity.secondary}
                      resourceId={item.principal_id}
                    />
                  );
                },
              },
              {
                label: t("Role"),
                render: (item) => (
                  <span className={settings.chip}>
                    {t(`role.${item.role_key}`, {
                      defaultValue: item.role_key,
                    })}
                  </span>
                ),
              },
              {
                label: t("Joined"),
                tone: "muted",
                render: (item) => (
                  <Timestamp value={item.created_at} relative />
                ),
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} members", { count: items.length })}
          >
            <Pagination page={page} next={bindings.data?.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        <Empty
          icon={<UsersIcon aria-hidden="true" />}
          title={t("No members")}
          description={t("Invite a teammate to start collaborating.")}
          action={action}
        />
      )}
    </div>
  );
}
function ChangeRole({
  item,
  scope,
  readVersion,
  triggerElement,
}: {
  item: Schema["RoleBinding"];
  scope: MembershipScope;
  readVersion: (basis: Schema["RoleBinding"]) => Promise<string>;
  triggerElement?: ReactElement;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [basis, setBasis] = useState(item),
    [role, setRole] = useState<Role>(
      roles.find((role) => role === item.role_key) ?? "viewer",
    ),
    [open, setOpen] = useState(false);
  const change = useMutation({
    mutationFn: async () => {
      const etag = await readVersion(basis);
      await client.http.PATCH("/api/v1/role-bindings/{binding_id}", {
        params: { path: { binding_id: item.id }, header: { "If-Match": etag } },
        body: { role },
      });
    },
    onSuccess: () => {
      void cache.invalidateQueries();
      setOpen(false);
    },
  });
  const reload = useMutation({
    mutationFn: () =>
      client.http
        .GET("/api/v1/role-bindings/{binding_id}", {
          params: { path: { binding_id: item.id } },
        })
        .then(data),
    onSuccess: (latest) => {
      setBasis(latest);
      setRole(roles.find((role) => role === latest.role_key) ?? "viewer");
      change.reset();
    },
  });
  const conflict =
    change.error instanceof ApiError && change.error.status === 412;
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (!change.isPending) {
          if (value) {
            setBasis(item);
            setRole(roles.find((role) => role === item.role_key) ?? "viewer");
          }
          setOpen(value);
          change.reset();
        }
      }}
      trigger={
        triggerElement ?? (
          <Button size="sm" variant="outline" type="button">
            {t("Change role")}
          </Button>
        )
      }
      size="md"
      title={t("Change role")}
      description={t("The new role takes effect when you save.")}
      closeLabel={t("Close")}
      open={open}
    >
      <form
        onSubmit={(event) => {
          event.preventDefault();
          change.mutate();
        }}
      >
        <ChoiceField
          placeholder={t("Select role")}
          value={role}
          className="min-w-0"
          onValueChange={(value) => {
            const role = roles.find((role) => role === value);
            if (role) setRole(role);
          }}
          label={t("Role")}
          options={roleOptions(scope.kind).map((value) => ({
            value,
            label: t(`role.${value}`, { defaultValue: value }),
          }))}
        />
        {conflict ? (
          <ConflictNotice
            title={t("This member changed")}
            description={t(
              "Someone updated this role while the dialog was open. Load the current role before saving.",
            )}
            recover={{
              label: t("Load current role"),
              onClick: () => reload.mutate(),
              pending: reload.isPending,
            }}
          />
        ) : (
          <ErrorNotice
            error={reload.error ?? change.error}
            retry={() => reload.mutate()}
          />
        )}
        <FormActions
          onCancel={() => setOpen(false)}
          pending={change.isPending}
        />
      </form>
    </ModalFrame>
  );
}
function AddMember({
  scope,
  organizationId,
}: {
  scope: MembershipScope;
  organizationId: string;
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [open, setOpen] = useState(false),
    [userId, setUserId] = useState(""),
    [role, setRole] = useState<Role>("viewer");
  const users = useQuery({
    queryKey: ["organization-users", organizationId],
    enabled: open,
    queryFn: ({ signal }) =>
      allPages((cursor) =>
        client.http
          .GET("/api/v1/organizations/{organization}/users", {
            params: {
              path: { organization: organizationId },
              query: { cursor, limit: 100 },
            },
            signal,
          })
          .then(data),
      ),
  });
  const add = useMutation({
    mutationFn: () =>
      client.http.POST("/api/v1/workspaces/{workspace}/role-bindings", {
        params: { path: { workspace: scope.id } },
        body: { principal_id: userId, role },
      }),
    onSuccess: () => {
      void cache.invalidateQueries();
      setOpen(false);
    },
  });
  return (
    <ModalFrame
      onOpenChange={setOpen}
      trigger={
        <Button variant="default" type="button">
          <PlusIcon size={14} />
          {t("Add member")}
        </Button>
      }
      size="md"
      title={t("Add existing member")}
      description={t(
        "Choose someone who already belongs to your organization.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      <form
        className={styles.form}
        onSubmit={(event) => {
          event.preventDefault();
          if (userId) add.mutate();
        }}
      >
        <FormField label={t("Member")}>
          <SearchPicker
            label={t("Member")}
            placeholder={t("Choose a member…")}
            emptyMessage={t("No members found")}
            value={userId}
            groups={[
              {
                label: t("Organization members"),
                options:
                  users.data?.map((user) => ({
                    value: user.id,
                    label: user.name,
                    description: user.email,
                  })) ?? [],
              },
            ]}
            onValueChange={setUserId}
          />
        </FormField>
        <ChoiceField
          placeholder={t("Select role")}
          value={role}
          className="min-w-0"
          onValueChange={(value) => {
            const role = roles.find((role) => role === value);
            if (role) setRole(role);
          }}
          label={t("Role")}
          options={roleOptions(scope.kind).map((value) => ({
            value,
            label: t(`role.${value}`, { defaultValue: value }),
          }))}
        />
        <ErrorNotice error={users.error ?? add.error} />
        <FormActions
          onCancel={() => setOpen(false)}
          pending={add.isPending}
          label={t("Add member")}
        />
      </form>
    </ModalFrame>
  );
}
