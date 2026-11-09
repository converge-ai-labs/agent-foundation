import { useQuery } from "@tanstack/react-query";
import { Button, DisclosureSection } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
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
import { runPath } from "../conversations/api";
import { OrganizationCandidate } from "./organization-candidate";
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
  const [selected, setSelected] = useState<Schema["MemoryOrganization"]>();
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
              onAccepted={setSelected}
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
                    <button
                      type="button"
                      className="block w-full truncate text-left hover:underline"
                      onClick={() => setSelected(item)}
                    >
                      {item.request.instructions}
                    </button>
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
      {selected && (
        <Section
          title={t("Task details")}
          description={selected.request.instructions}
          actions={
            <Button
              variant="ghost"
              size="sm"
              onClick={() => setSelected(undefined)}
            >
              {t("Close")}
            </Button>
          }
        >
          <div className="grid gap-5">
            <Link
              className="text-xs underline"
              to={runPath(basePath, selected, "debug")}
            >
              {t("Open run and usage")}
            </Link>
            <dl className="grid gap-2 text-xs sm:grid-cols-2">
              <div>
                <dt className="text-muted-foreground">
                  {t("Organizer model")}
                </dt>
                <dd>{selected.request.model}</dd>
              </div>
              <div>
                <dt className="text-muted-foreground">
                  {t("Frozen source runs")}
                </dt>
                <dd>{selected.sources.length}</dd>
              </div>
            </dl>
            {selected.sources.length > 0 && (
              <DisclosureSection title={t("Conversation sources")}>
                <ul className="grid gap-2 text-xs">
                  {selected.sources.map((source) => (
                    <li key={source.run_id}>
                      <Link to={runPath(basePath, source, "debug")}>
                        {source.run_id}
                      </Link>{" "}
                      · <Timestamp value={source.sealed_at} />
                    </li>
                  ))}
                </ul>
              </DisclosureSection>
            )}
            {selected.candidate_memory_id ? (
              <OrganizationCandidate key={selected.id} id={selected.id} />
            ) : (
              <p className="text-xs text-muted-foreground">
                {t(
                  "This task writes directly. Inspect file history for committed changes.",
                )}
              </p>
            )}
          </div>
        </Section>
      )}
    </div>
  );
}
