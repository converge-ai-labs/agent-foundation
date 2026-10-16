import { useQuery, useQueryClient } from "@tanstack/react-query";
import { Button, DisclosureSection } from "a13n-ui";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, type Schema } from "../../shared/api";
import { Pagination, ResourceTable, useCursor } from "../../shared/collection";
import {
  ErrorNotice,
  ErrorToast,
  Loading,
  Timestamp,
} from "../../shared/feedback";
import { Confirm } from "../../shared/dialogs";
import { JsonView } from "../../shared/forms";
import { useAgentComposer } from "./composer";
import { editableAgent, useModelsByKey } from "./queries";
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
    { workspace, can } = useWorkspace(),
    cache = useQueryClient(),
    page = useCursor(),
    composer = useAgentComposer(),
    models = useModelsByKey();
  const [selected, setSelected] = useState<Schema["AgentRevision"]>();
  // Revisions name their model by ID; versions show the model's key.
  const modelKey = (revision: Schema["AgentRevision"]) =>
    models.get(revision.config.model)?.key ?? revision.config.model;
  const query = useQuery({
    queryKey: ["agent-revisions", workspace.id, agent.id, page.cursor],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/agents/{agent_id}/revisions", {
          params: {
            path: { agent_id: agent.id },
            query: { cursor: page.cursor, limit: 20 },
          },
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
                v{item.number}{" "}
                {item.id === agent.default_revision_id && (
                  <span className="text-xs text-muted-foreground">
                    {t("Default version")}
                  </span>
                )}
              </Button>
            ),
          },
          { label: t("Model"), render: modelKey },
          {
            label: t("Version note"),
            render: (item) => item.note || "—",
          },
          {
            label: t("Created"),
            tone: "muted",
            render: (item) => <Timestamp value={item.created_at} />,
          },
          {
            label: t("Created by"),
            tone: "muted",
            render: (item) => item.created_by_id,
          },
          {
            label: t("Actions"),
            align: "right",
            render: (item) =>
              can("write") &&
              editableAgent(agent) &&
              item.id !== agent.default_revision_id && (
                <Confirm
                  subject={`${agent.name} · v${item.number}`}
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
                    await client
                      .workspace(workspace.id)
                      .POST(
                        "/api/v1/agents/{agent_id}/revisions/{revision_id}/set-default",
                        {
                          params: {
                            path: {
                              agent_id: agent.id,
                              revision_id: item.id,
                            },
                          },
                          headers: ifMatch(etag),
                        },
                      )
                      .then(data);
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
      {composer.setup}
      <ErrorToast error={composer.error} />
      {selected && (
        <section>
          {composer.available && (
            <Button
              type="button"
              variant="link"
              loading={composer.pending}
              onClick={() => composer.start({ agent, revision: selected })}
            >
              {t("Configure from this version")}
            </Button>
          )}
          <h3>
            {t("Version")} {selected.number}
          </h3>
          <dl className="grid gap-3 my-4">
            <div>
              <dt className={styles.muted}>{t("Model")}</dt>
              <dd>{modelKey(selected)}</dd>
            </div>
            {selected.note && (
              <div>
                <dt className={styles.muted}>{t("Version note")}</dt>
                <dd>{selected.note}</dd>
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
