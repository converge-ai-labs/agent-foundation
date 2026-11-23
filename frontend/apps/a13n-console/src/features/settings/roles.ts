/** Membership is granted on a workspace or on the organization itself. */
export type MembershipScope = {
  kind: "workspace" | "organization";
  id: string;
};

/** A grant at organization scope applies its role to every workspace. */
export const roles = ["viewer", "runner", "builder", "admin"] as const;

export type Role = (typeof roles)[number];

/** Keep the held role selectable when a distribution defines additional roles. */
export function rolesIncluding(current?: string | null): readonly string[] {
  return current && !roles.some((role) => role === current)
    ? [...roles, current]
    : roles;
}
