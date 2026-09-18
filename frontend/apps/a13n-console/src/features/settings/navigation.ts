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

/**
 * One settings section: its URL segment, how it reads in the navigation, and
 * the title and one-line description the content column carries.
 */
export type SettingsSectionDefinition = {
  /** URL segment under the scope's settings path. */
  value: string;
  /** Navigation label. */
  label: string;
  /** Content title when it differs from the navigation label. */
  title?: string;
  description?: string;
  /** Forms keep the 760px column; collections need room for their table. */
  layout?: "form" | "collection";
  icon: Icon;
  permission?: string;
};

const sections: Record<SettingsScope, SettingsSectionDefinition[]> = {
  personal: [
    {
      value: "preferences",
      layout: "form",
      label: "Preferences",
      description: "Choose how Console looks and feels.",
      icon: SlidersHorizontalIcon,
    },
    {
      value: "profile",
      layout: "form",
      label: "Profile",
      description: "Manage your name and image.",
      icon: UserIcon,
    },
    {
      value: "security",
      layout: "form",
      label: "Security",
      description: "Manage how you sign in and keep your account secure.",
      icon: ShieldCheckIcon,
    },
    {
      value: "sessions",
      label: "Login sessions",
      description: "Manage browsers signed in to your account.",
      icon: MonitorIcon,
    },
    {
      value: "activity",
      label: "Activity",
      description: "Review recent security activity on your account.",
      icon: PulseIcon,
    },
  ],
  workspace: [
    {
      value: "general",
      layout: "form",
      label: "General",
      description: "Name this workspace and control its address.",
      icon: GearSixIcon,
    },
    {
      value: "providers",
      label: "Providers",
      icon: SlidersIcon,
    },
    {
      value: "members",
      label: "Members",
      description: "Manage the people who can access this workspace.",
      icon: UsersIcon,
      permission: "role_binding.manage",
    },
    {
      value: "invitations",
      label: "Invitations",
      description: "Invite people and manage pending invitations.",
      icon: EnvelopeIcon,
      permission: "invitation.manage",
    },
    {
      value: "api-keys",
      label: "My API keys",
      description:
        "Your own credentials for this workspace. Keep them private.",
      icon: KeyIcon,
    },
    {
      value: "member-keys",
      label: "Member keys",
      description: "Review and revoke workspace members’ API keys.",
      icon: IdentificationBadgeIcon,
      permission: "api_key.manage",
    },
    {
      value: "service-accounts",
      label: "Service accounts",
      description: "Dedicated identities for applications and automation.",
      icon: RobotIcon,
      permission: "service_account.manage",
    },
    {
      value: "audit",
      label: "Audit",
      description: "Review changes to access and account security.",
      icon: ClipboardTextIcon,
      permission: "security_audit.read",
    },
  ],
  organization: [
    {
      value: "general",
      layout: "form",
      label: "General",
      description: "Name this organization and control its address.",
      icon: GearSixIcon,
    },
    {
      value: "members",
      label: "Members",
      description: "Manage the people who belong to this organization.",
      icon: UsersIcon,
    },
    {
      value: "invitations",
      label: "Invitations",
      description: "Invite people and manage pending invitations.",
      icon: EnvelopeIcon,
    },
    {
      value: "models",
      label: "Models",
      description: "Models available across your organization.",
      icon: CubeIcon,
    },
    {
      value: "environments",
      label: "Environment templates",
      description: "Shared templates for agent execution.",
      icon: TerminalWindowIcon,
    },
    {
      value: "providers",
      label: "Providers",
      icon: SlidersIcon,
    },
    {
      value: "workspaces",
      label: "Workspaces",
      description: "Separate resources, members, and work into workspaces.",
      icon: StackIcon,
    },
    {
      value: "audit",
      label: "Audit",
      description: "Review changes to access and account security.",
      icon: ClipboardTextIcon,
    },
  ],
};

/**
 * Section labels by scope and URL segment, so the document title can name the
 * settings section a reader is on.
 */
export const settingsSectionLabels: Record<
  SettingsScope,
  Record<string, string>
> = {
  personal: Object.fromEntries(
    sections.personal.map((item) => [item.value, item.label]),
  ),
  workspace: Object.fromEntries(
    sections.workspace.map((item) => [item.value, item.label]),
  ),
  organization: Object.fromEntries(
    sections.organization.map((item) => [item.value, item.label]),
  ),
};

/** `?section=` values that shipped before sections became addressable pages. */
const legacy: Record<string, string> = {
  profile: "general",
  "personal-keys": "api-keys",
  accounts: "service-accounts",
};

/**
 * Resolves a `?section=` value, or an unknown path segment, to a section this
 * scope actually renders. Personal settings keep `profile` as their own page.
 */
export function resolveSection(
  scope: SettingsScope,
  value: string | null | undefined,
): string | undefined {
  if (!value) return undefined;
  const available = sections[scope];
  if (available.some((item) => item.value === value)) return value;
  if (scope === "personal") return undefined;
  const mapped = legacy[value];
  return available.some((item) => item.value === mapped) ? mapped : undefined;
}

export function useSettingsNavigation() {
  const { workspace, organization, can, organizationAdmin } = useAccess();
  return [
    {
      scope: "personal" as const,
      label: "Personal",
      name: undefined,
      path: "/settings",
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
