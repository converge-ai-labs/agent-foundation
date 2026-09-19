import {
  Button,
  ChoiceField,
  FormField,
  Input,
  MenuItem,
  ModalFrame,
} from "a13n-ui";

import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState, type ReactElement } from "react";
import { PageActions } from "../../shared/page";

import {
  EnvelopeIcon,
  PaperPlaneTiltIcon,
  ProhibitIcon,
} from "@phosphor-icons/react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { data, type Schema } from "../../shared/api";
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
import { FormActions, SecretReveal } from "../../shared/forms";
import styles from "../../shared/shared.module.css";
import settings from "./settings.module.css";
import { roleOptions, type MembershipScope } from "./roles";

function invitationState(item: Schema["Invitation"]) {
  if (item.accepted_at) return "accepted";
  if (item.revoked_at) return "revoked";
  if (new Date(item.expires_at).getTime() < Date.now()) return "expired";
  return "pending";
}

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
  const items = query.data?.items ?? [];
  const invite = <InvitationEditor scope={scope} />;
  return (
    <div className={styles.stack}>
      <PageActions>{invite}</PageActions>
      {query.isPending ? (
        <Loading variant="table" columns={5} rows={5} />
      ) : query.error ? (
        <ErrorNotice error={query.error} />
      ) : items.length ? (
        <>
          <ResourceTable
            items={items}
            caption={t("Invitations")}
            rowMenuLabel={t("Invitation actions")}
            rowMenu={(item) =>
              item.accepted_at || item.revoked_at ? null : (
                <>
                  <InvitationEditor
                    scope={scope}
                    invitation={item}
                    triggerElement={
                      <MenuItem closeOnClick={false}>
                        <PaperPlaneTiltIcon size={14} />
                        {t("Resend")}
                      </MenuItem>
                    }
                  />
                  <Confirm
                    subject={item.email}
                    title={t("Revoke invitation")}
                    description={t("The invitation link will stop working.")}
                    triggerElement={
                      <MenuItem closeOnClick={false} variant="destructive">
                        <ProhibitIcon size={14} />
                        {t("Revoke")}
                      </MenuItem>
                    }
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
                </>
              )
            }
            columns={[
              {
                label: t("Email"),
                tone: "primary",
                render: (item) => (
                  <ResourceIdentity
                    icon={<EnvelopeIcon size={15} aria-hidden="true" />}
                    name={item.email}
                    resourceId={item.id}
                  />
                ),
              },
              {
                label: t("Role"),
                render: (item) => (
                  <span className={settings.chip}>
                    {item.grants
                      .map((grant) =>
                        t(`role.${grant.role_key}`, {
                          defaultValue: grant.role_key,
                        }),
                      )
                      .join(", ")}
                  </span>
                ),
              },
              {
                label: t("Status"),
                render: (item) => <StatePill state={invitationState(item)} />,
              },
              {
                label: t("Sent"),
                tone: "muted",
                render: (item) => (
                  <Timestamp value={item.created_at} relative />
                ),
              },
              {
                label: t("Expires"),
                tone: "muted",
                render: (item) => <Timestamp value={item.expires_at} />,
              },
            ]}
          />
          <CollectionFooter
            count={t("{{count}} invitations", { count: items.length })}
          >
            <Pagination page={page} next={query.data.next_cursor} />
          </CollectionFooter>
        </>
      ) : (
        <Empty
          icon={<EnvelopeIcon aria-hidden="true" />}
          title={t("No invitations")}
          description={t("Invite a teammate to start collaborating.")}
          action={invite}
        />
      )}
    </div>
  );
}

/**
 * Sends a new invitation, or replaces the link on an existing one. The result
 * is a single-use URL the service shows once, or the delivery it attempted.
 */
export function InvitationEditor({
  scope,
  invitation,
  triggerElement,
}: {
  scope: MembershipScope;
  invitation?: Schema["Invitation"];
  triggerElement?: ReactElement;
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
        triggerElement ?? (
          <Button variant="default" type="button">
            <PaperPlaneTiltIcon size={14} />
            {t("Invite")}
          </Button>
        )
      }
      size="md"
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
          {mutation.data.invitation_url ? (
            <SecretReveal
              value={mutation.data.invitation_url}
              label={t("Invitation link")}
              wrap
              caution={t(
                "Share this link with the person you invited. It is shown only once.",
              )}
            />
          ) : (
            <>
              <StatePill state={mutation.data.delivery} />
              <p className={settings.note}>
                {t(
                  mutation.data.delivery === "sent"
                    ? "Invitation sent"
                    : "Email delivery failed. Retry when delivery is available.",
                )}
              </p>
            </>
          )}
          <footer className={styles.formActions}>
            <Button type="button" onClick={() => setOpen(false)}>
              {t("Done")}
            </Button>
          </footer>
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
                  label: t(`role.${value}`, { defaultValue: value }),
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
