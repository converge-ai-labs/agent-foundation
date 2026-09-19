import { useQuery } from "@tanstack/react-query";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../../auth/context";
import { useWorkspace } from "../../../layout/workspace";
import { data, workspaceHeaders, type Schema } from "../../../shared/api";
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

/** Agents whose current configuration depends on this skill. */
export function UsedByAgents({ skill }: { skill: Schema["Skill"] }) {
  const { basePath } = useWorkspace(),
    client = useClient(),
    { t } = useTranslation(),
    page = useCursor();
  const query = useQuery({
    queryKey: [
      "skills",
      skill.workspace_id,
      skill.id,
      "references",
      page.cursor,
    ],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/skills/{skill_id}/references", {
          params: {
            path: { skill_id: skill.id },
            query: { cursor: page.cursor },
          },
          headers: workspaceHeaders(skill.workspace_id),
          signal,
        })
        .then(data),
  });
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
      <p className={shared.muted}>
        {t(
          "Current revisions of unarchived agents referencing this skill prevent deletion.",
        )}
      </p>
      <ListRows>
        {query.data.items.map((reference) => (
          <ListRow
            key={reference.agent_id}
            icon={
              <AgentAvatar
                name={reference.agent_name}
                id={reference.agent_id}
                className="size-8 rounded-[inherit]"
              />
            }
            name={
              <Link to={`${basePath}/agents/${reference.agent_key}`}>
                {reference.agent_name}
              </Link>
            }
            secondary={reference.agent_key}
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
