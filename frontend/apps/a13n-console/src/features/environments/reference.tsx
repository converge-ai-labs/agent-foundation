import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { CopyableId } from "../../shared/identity";
import { ResourceIdentity } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { environmentQuery } from "./api";
import { EnvironmentDetails } from "./instance-details";

export function EnvironmentReference({ id }: { id: string }) {
  const client = useClient();
  const { can } = useWorkspace();
  const allowed = can("environment.read");
  const query = useQuery({ ...environmentQuery(client, id), enabled: allowed });
  if (!allowed) return <CopyableId value={id} />;
  if (query.isPending) return <Loading variant="detail" />;
  if (!query.data)
    return (
      <>
        <CopyableId value={id} />
        <ErrorNotice error={query.error} />
      </>
    );
  const environment = query.data.value;
  return (
    <div className="flex flex-wrap items-center gap-2">
      <ResourceIdentity name={environment.name} resourceId={environment.id} />
      <EnvironmentDetails environment={environment} />
    </div>
  );
}
