import { ResourceReference } from "../identity";

export function ResourceModalTitle({ name, id }: { name: string; id: string }) {
  return (
    <span className="flex min-w-0 items-center gap-2">
      <span className="min-w-0 truncate" title={name}>
        {name}
      </span>
      <ResourceReference id={id} />
    </span>
  );
}
