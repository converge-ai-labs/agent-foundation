export function providersPath(
  category: string,
  scope = "workspace",
  workspaceKey?: string,
) {
  const params = new URLSearchParams({ category, scope });
  if (workspaceKey) params.set("workspace", workspaceKey);
  return `/providers?${params}`;
}
