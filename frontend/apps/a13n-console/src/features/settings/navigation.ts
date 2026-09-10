import {
  Activity,
  Boxes,
  KeyRound,
  Layers,
  Mail,
  Monitor,
  Settings,
  Settings2,
  Shield,
  SlidersHorizontal,
  User,
  Users,
  type LucideIcon,
} from "lucide-react";
import { workspacePath } from "../../shared/paths";
import { useAccess } from "../../layout/workspace";

export type SettingsScope = "personal" | "workspace" | "organization";
type Section = {
  value: string;
  label: string;
  icon: LucideIcon;
  permission?: string;
  href?: string;
};
const sections: Record<SettingsScope, Section[]> = {
  personal: [
    { value: "preferences", label: "Preferences", icon: SlidersHorizontal },
    { value: "profile", label: "Profile", icon: User },
    { value: "security", label: "Security", icon: Shield },
    { value: "sessions", label: "Sessions", icon: Monitor },
    { value: "activity", label: "Activity", icon: Activity },
  ],
  workspace: [
    { value: "profile", label: "General", icon: Settings },
    {
      value: "members",
      label: "Members",
      icon: Users,
      permission: "role_binding.manage",
    },
    {
      value: "invitations",
      label: "Invitations",
      icon: Mail,
      permission: "invitation.manage",
    },
    { value: "personal-keys", label: "My API keys", icon: KeyRound },
    {
      value: "member-keys",
      label: "Member keys",
      icon: KeyRound,
      permission: "api_key.manage",
    },
    {
      value: "accounts",
      label: "Service accounts",
      icon: Shield,
      permission: "service_account.manage",
    },
    {
      value: "audit",
      label: "Audit",
      icon: Activity,
      permission: "security_audit.read",
    },
  ],
  organization: [
    { value: "profile", label: "General", icon: Settings },
    { value: "members", label: "Members", icon: Users },
    { value: "invitations", label: "Invitations", icon: Mail },
    { value: "models", label: "Models", icon: Boxes },
    {
      value: "environments",
      label: "Environment templates",
      icon: Monitor,
    },
    {
      value: "providers",
      label: "Providers",
      icon: Settings2,
      href: "/providers?scope=organization",
    },
    { value: "workspaces", label: "Workspaces", icon: Layers },
    { value: "audit", label: "Audit", icon: Activity },
  ],
};
export function useSettingsNavigation() {
  const { workspace, organization, can, organizationAdmin } = useAccess();
  return [
    {
      scope: "personal" as const,
      label: "Personal",
      name: undefined,
      path: "/settings/profile",
      sections: sections.personal,
    },
    ...(workspace
      ? [
          {
            scope: "workspace" as const,
            label: "Workspace",
            name: workspace.name,
            path: `${workspacePath(workspace)}/settings`,
            sections: sections.workspace.filter(
              (item) => !item.permission || can(item.permission),
            ),
          },
        ]
      : []),
    ...(organizationAdmin
      ? [
          {
            scope: "organization" as const,
            label: "Organization",
            name: organization.name,
            path: "/organization/settings",
            sections: sections.organization,
          },
        ]
      : []),
  ];
}
