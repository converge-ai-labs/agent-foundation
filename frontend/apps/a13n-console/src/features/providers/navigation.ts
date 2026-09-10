export function providersPath(
  category: string,
  scope = "workspace",
  workspaceKey?: string,
) {
  const params = new URLSearchParams({ section: "providers", category });
  const path =
    scope === "organization"
      ? "/organization/settings"
      : `/workspace/${encodeURIComponent(workspaceKey!)}/settings`;
  return `${path}?${params}`;
}
