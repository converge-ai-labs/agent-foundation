export const resourceKeyPattern = "[a-z0-9]+(-[a-z0-9]+)*";
export function isResourceKey(value: string): boolean {
  return (
    value.length <= 64 && new RegExp(`^${resourceKeyPattern}$`).test(value)
  );
}

export function workspacePath(
  organization: { key: string },
  workspace: { key: string },
): string {
  return `/${organization.key}/${workspace.key}`;
}
