import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, DisclosureSection } from "a13n-ui";
import { useState } from "react";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  commandHeaders,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Confirm } from "../../shared/dialogs";
import { JsonView } from "../../shared/forms";
import { useIdempotency } from "../../shared/idempotency";
import styles from "../../shared/shared.module.css";

export function AgentVersions({
  agent,
  etag,
  onDefaultChanged,
}: {
  agent: Schema["Agent"];
  etag?: string;
  onDefaultChanged: () => Promise<void>;
}) {
  const client = useClient(),
    { t } = useTranslation(),
    { workspace, can, basePath } = useWorkspace(),
    cache = useQueryClient(),
    page = useCursor(),
    idempotency = useIdempotency();
  const [selected, setSelected] = useState<Schema["AgentRevision"]>();
  const query = useQuery({
    queryKey: ["agent-revisions", workspace.id, agent.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/agents/{agent}/revisions", {
          params: {
            path: { workspace: workspace.id, agent: agent.id },
            query: { cursor: page.cursor, limit: 20 },
          },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
  });
  if (query.isPending) return <Loading variant="table" columns={6} rows={5} />;
  if (!query.data) return <ErrorNotice error={query.error} />;
  return (
    <div className={styles.stack}>
      <p className={styles.muted}>
        {t(
          "Versions are immutable. Setting the default does not create a version.",
        )}
      </p>
      <ResourceTable
        items={query.data.items}
        columns={[
          {
            label: t("Version"),
            tone: "primary",
            render: (item) => (
              <Button
                variant="ghost"
                onClick={() => setSelected(item)}
                type="button"
              >
                v{item.version}{" "}
                {item.id === agent.default_revision_id && (
                  <span className="text-xs text-muted-foreground">
                    {t("Default version")}
                  </span>
                )}
              </Button>
            ),
          },
          { label: t("Model"), render: (item) => item.config.model.model_key },
          {
            label: t("Version note"),
            render: (item) => item.change_summary || "—",
          },
          {
            label: t("Created"),
            tone: "muted",
            render: (item) => <Timestamp value={item.created_at} />,
          },
          {
            label: t("Created by"),
            tone: "muted",
            render: (item) => item.created_by.principal_id,
          },
          {
            label: t("Actions"),
            align: "right",
            render: (item) =>
              can("agent.revision.create") &&
              item.id !== agent.default_revision_id && (
                <Confirm
                  subject={`${agent.name} · v${item.version}`}
                  triggerVariant="ghost"
                  title={t("Set as default")}
                  description={t(
                    "Future runs will use this version unless another version is selected.",
                  )}
                  trigger={t("Set as default")}
                  action={async () => {
                    if (!etag)
                      throw new Error(
                        t(
                          "Version information is unavailable. Reload this page.",
                        ),
                      );
                    await client.http
                      .POST(
                        "/api/v1/workspaces/{workspace}/agents/{agent}/revisions/{revision_id}/default",
                        {
                          params: {
                            path: {
                              workspace: workspace.id,
                              agent: agent.id,
                              revision_id: item.id,
                            },
                            header: {
                              ...commandHeaders(
                                workspace.id,
                                idempotency.forBody({ revision: item.id }),
                              ),
                              "If-Match": etag,
                            },
                          },
                          body: {},
                        },
                      )
                      .then(data);
                    idempotency.reset();
                    await onDefaultChanged();
                    await cache.invalidateQueries({
                      queryKey: ["agent-revisions", workspace.id, agent.id],
                    });
                  }}
                />
              ),
          },
        ]}
      />
      <Pagination page={page} next={query.data.next_cursor} />
      {selected && (
        <section>
          {can("agent.revision.create") && (
            <Link
              to={`${basePath}/configuration/new?agent=${agent.id}&revision=${selected.id}`}
            >
              {t("Configure from this version")}
            </Link>
          )}
          <h3>
            {t("Version")} {selected.version}
          </h3>
          <dl className="grid gap-3 my-4">
            <div>
              <dt className={styles.muted}>{t("Model")}</dt>
              <dd>{selected.config.model.model_key}</dd>
            </div>
            {selected.change_summary && (
              <div>
                <dt className={styles.muted}>{t("Version note")}</dt>
                <dd>{selected.change_summary}</dd>
              </div>
            )}
            <div>
              <dt className={styles.muted}>{t("Instructions")}</dt>
              <dd className="whitespace-pre-wrap">
                {selected.config.instructions || "—"}
              </dd>
            </div>
          </dl>
          <DisclosureSection title={t("Configuration details")}>
            <JsonView value={selected.config} />
          </DisclosureSection>
        </section>
      )}
    </div>
  );
}
