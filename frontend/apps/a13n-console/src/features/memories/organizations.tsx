import { ArrowRightIcon } from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { Link, useNavigate } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import type { Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Section } from "../../shared/page";
import { OrganizationRequest } from "./organization-request";
import {
  organizationQueries,
  type OrganizationTarget,
} from "./organizations-api";

export function MemoryOrganizations({
  target,
  model,
  enabled = true,
}: {
  target: OrganizationTarget;
  model?: string;
  enabled?: boolean;
}) {
  const client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor(target.id);
  const query = useQuery(
    organizationQueries(client, workspace.id).page(target, page.cursor),
  );
  const navigate = useNavigate();
  const taskPath = (item: Schema["MemoryOrganization"]) =>
    `${basePath}/memory-organizations/${item.id}/review`;
  return (
    <div className="grid gap-8">
      <Section
        title={t("Organization tasks")}
        description={t(
          "Organize saved files or extract knowledge from selected conversations. Candidate output is reviewed before publication.",
        )}
        actions={
          enabled &&
          can("run") &&
          can("write") && (
            <OrganizationRequest
              target={target}
              model={model}
              onAccepted={(item) => navigate(taskPath(item))}
            />
          )
        }
      >
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
        {query.isPending ? (
          <Loading variant="table" columns={3} />
        ) : query.data?.items.length ? (
          <>
            <ResourceTable
              caption={t("Organization tasks")}
              items={query.data.items}
              columns={[
                {
                  label: t("Task"),
                  tone: "primary",
                  render: (item) => (
                    <Link
                      className="block max-w-[48ch] truncate text-left hover:underline"
                      to={taskPath(item)}
                    >
                      {item.request.instructions}
                    </Link>
                  ),
                },
                {
                  label: t("Output"),
                  render: (item) =>
                    t(item.candidate_memory_id ? "Candidate" : "Direct"),
                },
                {
                  label: t("Created"),
                  tone: "muted",
                  render: (item) => <Timestamp value={item.created_at} />,
                },
                {
                  label: t("Review"),
                  render: (item) => (
                    <Button
                      variant="ghost"
                      size="sm"
                      render={<Link to={taskPath(item)} />}
                    >
                      {t(
                        item.candidate_memory_id
                          ? "Review candidate"
                          : "View task",
                      )}
                      <ArrowRightIcon size={14} />
                    </Button>
                  ),
                },
              ]}
            />
            <CollectionFooter>
              <Pagination page={page} next={query.data.next_cursor} />
            </CollectionFooter>
          </>
        ) : (
          !query.error && (
            <Empty
              title={t("No organization tasks yet")}
              description={t(
                "Start a task to curate saved files or learn from selected conversations.",
              )}
            />
          )
        )}
      </Section>
    </div>
  );
}
