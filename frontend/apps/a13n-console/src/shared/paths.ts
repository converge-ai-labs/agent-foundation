export const resourceKeyPattern = "[a-z0-9]+(-[a-z0-9]+)*";
export function isResourceKey(value: string): boolean {
  return (
    value.length <= 64 && new RegExp(`^${resourceKeyPattern}$`).test(value)
  );
}

export function workspacePath(workspace: { key: string }): string {
  return `/workspace/${workspace.key}`;
}
