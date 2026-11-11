export function parseHosts(text: string): string[] {
  return [
    ...new Set(
      text
        .split(/[\s,]+/)
        .filter(Boolean)
        .map((host) => host.replace(/\.$/, "").toLowerCase()),
    ),
  ];
}

export function validHosts(hosts: string[]) {
  return (
    hosts.length > 0 &&
    hosts.length <= 256 &&
    hosts.every(
      (host) =>
        host.length <= 253 &&
        !/^\d+\.\d+\.\d+\.\d+$/.test(host) &&
        host
          .split(".")
          .every((label) =>
            /^[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?$/.test(label),
          ),
    )
  );
}

export function validToken(value: string) {
  const bytes = new TextEncoder().encode(value).length;
  return (
    bytes > 0 &&
    bytes <= 8192 &&
    !Array.from(value).some(
      (char) => char.charCodeAt(0) < 32 || char.charCodeAt(0) === 127,
    )
  );
}

export type MetadataDraft = { name: string; description: string };
export function metadataPatch(draft: MetadataDraft, basis: MetadataDraft) {
  return {
    ...(draft.name.trim() !== basis.name ? { name: draft.name.trim() } : {}),
    ...(draft.description !== basis.description
      ? { description: draft.description }
      : {}),
  };
}

export function rebaseMetadata(
  draft: MetadataDraft,
  basis: MetadataDraft,
  latest: MetadataDraft,
): MetadataDraft {
  return {
    name: latest.name,
    description: latest.description,
    ...metadataPatch(draft, basis),
  };
}
