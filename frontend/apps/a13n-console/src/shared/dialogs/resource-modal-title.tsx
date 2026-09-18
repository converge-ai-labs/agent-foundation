import { ResourceKeyChip, ResourceReference } from "../identity";

export function ResourceModalTitle({
  name,
  id,
  resourceKey,
}: {
  name: string;
  id: string;
  resourceKey?: string;
}) {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <span className="min-w-0 truncate" title={name}>
        {name}
      </span>
      {resourceKey && <ResourceKeyChip value={resourceKey} />}
      <ResourceReference id={id} />
    </span>
  );
}
