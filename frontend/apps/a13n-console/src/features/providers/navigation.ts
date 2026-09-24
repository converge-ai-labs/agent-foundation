export function providersPath(
  category: string,
  scope = "workspace",
  workspaceKey?: string,
) {
  const path =
    scope === "organization"
      ? "/organization/settings"
      : `/workspace/${encodeURIComponent(workspaceKey!)}/settings`;
  return `${path}/providers?${new URLSearchParams({ category })}`;
}
