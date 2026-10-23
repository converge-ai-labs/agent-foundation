import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useAccess, useWorkspace } from "../../layout/workspace";
import { Empty } from "../../shared/collection";
import { Page } from "../../shared/page";
import { ProvidersPage } from "../providers/page";
import { MediaUnderstandingDefaults } from "../models/media-understanding";
import { ServiceAccountDetail, ServiceAccounts } from "./accounts";
import { Audit } from "./audit";
import { ApiKeys } from "./keys";
import { SettingsLayout } from "./layout";
import { MemberSettings } from "./members";
import { Profile } from "./profile";
import { DeleteWorkspace, Workspaces } from "./workspaces";
import { Webhooks } from "./webhooks/list";
import { WebhookDetail } from "./webhooks/detail";

export function WorkspaceSettings() {
  const { workspace, can, organizationCan, basePath } = useWorkspace();
  const { accountId, subscriptionId, section } = useParams();
  const { t } = useTranslation();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const scope = { kind: "workspace", id: workspace.id } as const;
  if ((subscriptionId || section === "webhooks") && !can("admin"))
    return (
      <Page title={t("Webhooks")}>
        <Empty
          title={t("Access unavailable")}
          description={t(
            "A workspace administrator can manage Webhooks and inspect their deliveries.",
          )}
        />
      </Page>
    );
  return (
    <SettingsLayout
      scope="workspace"
      section={
        subscriptionId ? "webhooks" : accountId ? "service-accounts" : undefined
      }
      heading={!accountId && !subscriptionId}
      content={{
        general: (
          <Profile
            target={scope}
            editable={can("admin")}
            danger={
              organizationCan("admin") &&
              !workspace.archived_at && (
                <DeleteWorkspace
                  workspace={workspace}
                  onSuccess={() => {
                    void cache.invalidateQueries();
                    void navigate("/", { replace: true });
                  }}
                />
              )
            }
          />
        ),
        "media-understanding": (
          <MediaUnderstandingDefaults key={workspace.id} />
        ),
        members: <MemberSettings scope={scope} />,
        "api-keys": <ApiKeys />,
        "member-keys": <ApiKeys memberKeys />,
        "service-accounts": accountId ? (
          <ServiceAccountDetail key={accountId} accountId={accountId} />
        ) : (
          <ServiceAccounts key={basePath} />
        ),
        audit: <Audit scope={scope} />,
        webhooks: subscriptionId ? (
          <WebhookDetail
            key={`${workspace.id}:${subscriptionId}`}
            subscriptionId={subscriptionId}
          />
        ) : (
          <Webhooks key={workspace.id} />
        ),
        providers: <ProvidersPage />,
      }}
    />
  );
}
export function OrganizationSettings() {
  const { t } = useTranslation(),
    { organization, organizationCan } = useAccess();
  const scope = { kind: "organization", id: organization.id } as const;
  if (!organizationCan("admin"))
    return (
      <Page title={t("Organization settings")}>
        <Empty
          title={t("Access unavailable")}
          description={t(
            "An organization administrator can manage these settings.",
          )}
        />
      </Page>
    );
  return (
    <SettingsLayout
      scope="organization"
      content={{
        general: <Profile target={scope} />,
        members: <MemberSettings scope={scope} />,
        workspaces: <Workspaces />,
        audit: <Audit scope={scope} />,
      }}
    />
  );
}
