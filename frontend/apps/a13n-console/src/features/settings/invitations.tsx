import { ResourceEditorButton } from "../../shared/identity";
import { ChoiceField, FormField, Input, ModalFrame } from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { PageActions } from "../../shared/page";

import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { Empty } from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { Confirm } from "../../shared/dialogs";
import { FormActions } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import { SecretReveal } from "./keys";
import { roleOptions, type MembershipScope } from "./members";

export function Invitations({ scope }: { scope: MembershipScope }) {
  const client = useClient(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: ["invitations", scope.kind, scope.id, page.cursor],
    queryFn: ({ signal }) =>
      scope.kind === "workspace"
        ? client.http
            .GET("/api/v1/workspaces/{workspace}/invitations", {
              signal,
              params: {
                path: { workspace: scope.id },
                query: { cursor: page.cursor, limit: 30 },
              },
            })
            .then(data)
        : client.http
            .GET("/api/v1/organizations/{organization}/invitations", {
              signal,
              params: {
                path: { organization: scope.id },
                query: { cursor: page.cursor, limit: 30 },
              },
            })
            .then(data),
  });
  return (
    <div className={styles.stack}>
      <PageActions>
        <InvitationEditor scope={scope} />
      </PageActions>
      {query.isPending ? (
        <Loading variant="table" columns={5} rows={5} />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            items={query.data.items}
            columns={[
              {
                label: t("Email"),
                tone: "primary",
                render: (item) => item.email,
              },
              {
                label: t("Role"),
                render: (item) =>
                  item.grants
                    .map((grant) =>
                      t(`role.${grant.role_key}`, {
                        defaultValue: grant.role_key,
                      }),
                    )
                    .join(", "),
              },
              {
                label: t("Expires"),
                tone: "muted",
                render: (item) => <Timestamp value={item.expires_at} />,
              },
              {
                label: t("Status"),
                render: (item) => (
                  <StatePill
                    state={
                      item.accepted_at
                        ? "accepted"
                        : item.revoked_at
                          ? "revoked"
                          : new Date(item.expires_at).getTime() < Date.now()
                            ? "expired"
                            : "pending"
                    }
                  />
                ),
              },
              {
                label: t("Actions"),
                align: "right",
                render: (item) =>
                  !item.accepted_at &&
                  !item.revoked_at && (
                    <div className={styles.actions}>
                      <InvitationEditor scope={scope} invitation={item} />
                      <Confirm
                        subject={item.email}
                        triggerVariant="ghost"
                        title={t("Revoke invitation")}
                        description={t(
                          "The invitation link will stop working.",
                        )}
                        trigger={t("Revoke")}
                        danger
                        action={() =>
                          client.http.POST(
                            "/api/v1/invitations/{invitation_id}/revoke",
                            {
                              params: { path: { invitation_id: item.id } },
                              body: { expected_version: item.version },
                            },
                          )
                        }
                      />
                    </div>
                  ),
              },
            ]}
          />
          <Pagination page={page} next={query.data.next_cursor} />
        </>
      ) : (
        <Empty
          title={t("No invitations")}
          description={t("Invite a teammate to start collaborating.")}
        />
      )}
    </div>
  );
}
function InvitationEditor({
  scope,
  invitation,
}: {
  scope: MembershipScope;
  invitation?: Schema["Invitation"];
}) {
  const { t } = useTranslation(),
    client = useClient(),
    cache = useQueryClient();
  const [open, setOpen] = useState(false),
    [email, setEmail] = useState(""),
    [role, setRole] = useState<Schema["ChangeRoleRequest"]["role"]>(
      scope.kind === "organization" ? "member" : "viewer",
    );
  const mutation = useMutation({
    gcTime: 0,
    mutationFn: async () => {
      if (invitation)
        return client.http
          .POST("/api/v1/invitations/{invitation_id}/resend", {
            params: { path: { invitation_id: invitation.id } },
            body: { expected_version: invitation.version },
          })
          .then(data);
      if (scope.kind === "workspace") {
        if (role === "member") throw new Error("Invalid workspace role");
        return client.http
          .POST("/api/v1/workspaces/{workspace}/invitations", {
            params: { path: { workspace: scope.id } },
            body: { email, role },
          })
          .then(data);
      }
      return client.http
        .POST("/api/v1/organizations/{organization}/invitations", {
          params: { path: { organization: scope.id } },
          body: {
            email,
            grants: [
              {
                resource_type: "organization",
                resource_id: scope.id,
                role_key: role,
              },
            ],
          },
        })
        .then(data);
    },
    onSuccess: () => {
      void cache.invalidateQueries({
        queryKey: ["invitations", scope.kind, scope.id],
      });
    },
  });
  return (
    <ModalFrame
      onOpenChange={(value) => {
        if (!mutation.isPending) {
          setOpen(value);
          mutation.reset();
        }
      }}
      trigger={
        <ResourceEditorButton
          editing={!!invitation}
          createLabel="Invite member"
          editLabel="Resend"
        />
      }
      size={"md"}
      title={t(invitation ? "Resend invitation" : "Invite member")}
      description={t(
        invitation
          ? "A new link replaces the previous invitation link."
          : "New members receive a single-use invitation link.",
      )}
      closeLabel={t("Close")}
      open={open}
    >
      {mutation.data ? (
        <div className={styles.stack}>
          <StatePill state={mutation.data.delivery} />
          {mutation.data.invitation_url ? (
            <SecretReveal value={mutation.data.invitation_url} />
          ) : (
            <p>
              {t(
                mutation.data.delivery === "sent"
                  ? "Invitation sent"
                  : "Email delivery failed. Retry when delivery is available.",
              )}
            </p>
          )}
        </div>
      ) : (
        <form
          className={styles.form}
          onSubmit={(event) => {
            event.preventDefault();
            mutation.mutate();
          }}
        >
          {!invitation && (
            <>
              <FormField className="min-w-0 w-full" label={t("Email address")}>
                <Input
                  required={true}
                  type="email"
                  value={email}
                  onChange={(event) => setEmail(event.target.value)}
                />
              </FormField>
              <ChoiceField
                placeholder={t("Select role")}
                value={role}
                className="min-w-0"
                onValueChange={(value) => {
                  const role = roleOptions(scope.kind).find(
                    (role) => role === value,
                  );
                  if (role) setRole(role);
                }}
                label={t("Role")}
                options={roleOptions(scope.kind).map((value) => ({
                  value,
                  label: t(`role.${value}`, {
                    defaultValue: value,
                  }),
                }))}
              />
            </>
          )}
          <ErrorNotice error={mutation.error} />
          <FormActions
            onCancel={() => setOpen(false)}
            pending={mutation.isPending}
            label={t(invitation ? "Resend invitation" : "Send invitation")}
          />
        </form>
      )}
    </ModalFrame>
  );
}
