import type { Schema } from "../../shared/api";

/** Membership is granted on a workspace or on the organization itself. */
export type MembershipScope = {
  kind: "workspace" | "organization";
  id: string;
};

export type Role = Schema["ChangeRoleRequest"]["role"];

export const roles: Role[] = ["member", "viewer", "runner", "builder", "admin"];

/** Organizations grant membership or administration; workspaces grant work. */
export function roleOptions(kind: MembershipScope["kind"]) {
  return roles.filter((role) =>
    kind === "organization"
      ? ["member", "admin"].includes(role)
      : role !== "member",
  );
}
