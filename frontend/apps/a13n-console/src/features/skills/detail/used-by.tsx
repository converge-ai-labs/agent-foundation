import { queryOptions, useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import type { Client } from "../../../service-client";
import { data, type Schema } from "../../../shared/api";
import {
  CollectionFooter,
  Empty,
  ListRow,
  ListRows,
  Pagination,
  useCursor,
} from "../../../shared/collection";
import { ErrorNotice, Loading } from "../../../shared/feedback";
import shared from "../../../shared/shared.module.css";
import { AgentAvatar } from "../../agents/avatar";
import { SkillIcon } from "../source";

/**
 * Unarchived agents with a revision that pins this skill, a page at a time.
 * The list's usage count reads the same first page.
 */
export function usedByQuery(
  client: Client,
  skill: Pick<Schema["Skill"], "id" | "workspace_id">,
  cursor?: string,
) {
  return queryOptions({
    queryKey: ["skills", skill.workspace_id, skill.id, "used-by", cursor],
    queryFn: ({ signal }) =>
      client
        .workspace(skill.workspace_id)
        .GET("/api/v1/agents", {
          params: {
            query: { skill_id: skill.id, archived: false, cursor },
          },
          signal,
        })
        .then(data),
  });
}

/** Agents whose configuration depends on this skill. */
export function UsedByAgents({ skill }: { skill: Schema["Skill"] }) {
  const { basePath } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery(usedByQuery(client, skill, page.cursor));
  if (query.isPending) return <Loading variant="list" rows={3} />;
  if (!query.data)
    return (
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
    );
  if (!query.data.items.length)
    return (
      <Empty
        icon={<SkillIcon size={20} />}
        title={t("No active references")}
        description={t("No current agent configuration uses this skill.")}
      />
    );
  return (
    <div className={shared.stack}>
      <ListRows>
        {query.data.items.map((agent) => (
          <ListRow
            key={agent.id}
            icon={
              <AgentAvatar
                name={agent.name}
                id={agent.id}
                url={agent.image_url}
                className="size-8 rounded-[inherit]"
              />
            }
            name={
              <Link to={`${basePath}/agents/${agent.id}`}>{agent.name}</Link>
            }
            secondary={agent.id}
          />
        ))}
      </ListRows>
      <CollectionFooter
        count={t("{{count}} agents on this page", {
          count: query.data.items.length,
        })}
      >
        <Pagination page={page} next={query.data.next_cursor} />
      </CollectionFooter>
    </div>
  );
}
