import { useQuery } from "@tanstack/react-query";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { ResourceIdentity } from "../../shared/collection";
import { ErrorNotice, Loading } from "../../shared/feedback";
import { CopyableId } from "../../shared/identity";
import { environmentQuery } from "./api";
import styles from "./environments.module.css";
import { EnvironmentDetails } from "./instance-details";

/** One environment referenced from somewhere else: its name, then a way in. */
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
    <div className={styles.providerCell}>
      <ResourceIdentity name={environment.name} resourceId={environment.id} />
      <EnvironmentDetails environment={environment} />
    </div>
  );
}
