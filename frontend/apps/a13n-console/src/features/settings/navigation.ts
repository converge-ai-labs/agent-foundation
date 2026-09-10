import {
  PulseIcon,
  IdentificationBadgeIcon,
  RobotIcon,
  ClipboardTextIcon,
  TerminalWindowIcon,
  CubeIcon,
  KeyIcon,
  StackIcon,
  EnvelopeIcon,
  MonitorIcon,
  GearSixIcon,
  SlidersIcon,
  ShieldCheckIcon,
  SlidersHorizontalIcon,
  UserIcon,
  UsersIcon,
  type Icon,
} from "@phosphor-icons/react";
import { workspacePath } from "../../shared/paths";
import { useAccess } from "../../layout/workspace";

export type SettingsScope = "personal" | "workspace" | "organization";
type Section = {
  value: string;
  label: string;
  icon: Icon;
  permission?: string;
  href?: string;
};
const sections: Record<SettingsScope, Section[]> = {
  personal: [
    { value: "preferences", label: "Preferences", icon: SlidersHorizontalIcon },
    { value: "profile", label: "Profile", icon: UserIcon },
    { value: "security", label: "Security", icon: ShieldCheckIcon },
    { value: "sessions", label: "Sessions", icon: MonitorIcon },
    { value: "activity", label: "Activity", icon: PulseIcon },
  ],
  workspace: [
    { value: "profile", label: "General", icon: GearSixIcon },
    { value: "providers", label: "Providers", icon: SlidersIcon },
    {
      value: "members",
      label: "Members",
      icon: UsersIcon,
      permission: "role_binding.manage",
    },
    {
      value: "invitations",
      label: "Invitations",
      icon: EnvelopeIcon,
      permission: "invitation.manage",
    },
    { value: "personal-keys", label: "My API keys", icon: KeyIcon },
    {
      value: "member-keys",
      label: "Member keys",
      icon: IdentificationBadgeIcon,
      permission: "api_key.manage",
    },
    {
      value: "accounts",
      label: "Service accounts",
      icon: RobotIcon,
      permission: "service_account.manage",
    },
    {
      value: "audit",
      label: "Audit",
      icon: ClipboardTextIcon,
      permission: "security_audit.read",
    },
  ],
  organization: [
    { value: "profile", label: "General", icon: GearSixIcon },
    { value: "members", label: "Members", icon: UsersIcon },
    { value: "invitations", label: "Invitations", icon: EnvelopeIcon },
    { value: "models", label: "Models", icon: CubeIcon },
    {
      value: "environments",
      label: "Environment templates",
      icon: TerminalWindowIcon,
    },
    {
      value: "providers",
      label: "Providers",
      icon: SlidersIcon,
    },
    { value: "workspaces", label: "Workspaces", icon: StackIcon },
    { value: "audit", label: "Audit", icon: ClipboardTextIcon },
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
