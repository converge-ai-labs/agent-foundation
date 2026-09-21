import { useTranslation } from "react-i18next";
import { useNavigate, useParams } from "react-router";
import { useQueryClient } from "@tanstack/react-query";
import { useAccess, useWorkspace } from "../../layout/workspace";
import { Empty } from "../../shared/collection";
import { Page } from "../../shared/page";
import { EnvironmentTemplates } from "../environments/templates";
import { ProvidersPage } from "../providers/page";
import { MediaUnderstandingDefaults } from "../models/media-understanding";
import { Models } from "../models/page";
import { ServiceAccountDetail, ServiceAccounts } from "./accounts";
import { Audit } from "./audit";
import { Invitations } from "./invitations";
import { ApiKeys } from "./keys";
import { SettingsLayout } from "./layout";
import { Members } from "./members";
import { Profile } from "./profile";
import { DeleteWorkspace, Workspaces } from "./workspaces";

export function WorkspaceSettings() {
  const { workspace, can, basePath } = useWorkspace();
  const { organizationAdmin } = useAccess();
  const { accountId } = useParams();
  const navigate = useNavigate();
  const cache = useQueryClient();
  const scope = { kind: "workspace", id: workspace.id } as const;
  return (
    <SettingsLayout
      scope="workspace"
      section={accountId ? "service-accounts" : undefined}
      heading={!accountId}
      content={{
        general: (
          <Profile
            target={scope}
            editable={can("role_binding.manage")}
            danger={
              organizationAdmin && (
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
        members: <Members scope={scope} />,
        invitations: <Invitations scope={scope} />,
        "api-keys": <ApiKeys />,
        "member-keys": <ApiKeys memberKeys />,
        "service-accounts": accountId ? (
          <ServiceAccountDetail key={accountId} accountId={accountId} />
        ) : (
          <ServiceAccounts key={basePath} />
        ),
        audit: <Audit scope={scope} />,
        providers: <ProvidersPage scope={scope} />,
      }}
    />
  );
}
export function OrganizationSettings() {
  const { t } = useTranslation(),
    { organization, organizationAdmin } = useAccess();
  const scope = { kind: "organization", id: organization.id } as const;
  if (!organizationAdmin)
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
        members: <Members scope={scope} />,
        invitations: <Invitations scope={scope} />,
        models: <Models scope={scope} />,
        environments: <EnvironmentTemplates scope={scope} />,
        workspaces: <Workspaces />,
        audit: <Audit scope={scope} />,
        providers: <ProvidersPage scope={scope} />,
      }}
    />
  );
}
