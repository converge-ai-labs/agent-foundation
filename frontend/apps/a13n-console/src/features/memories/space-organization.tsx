import { ArrowRightIcon, ArrowsClockwiseIcon } from "@phosphor-icons/react";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { useTranslation } from "react-i18next";
import { Link } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, type Schema } from "../../shared/api";
import {
  CollectionFooter,
  Empty,
  Pagination,
  ResourceTable,
  useCursor,
} from "../../shared/collection";
import {
  ErrorNotice,
  Loading,
  StatePill,
  Timestamp,
} from "../../shared/feedback";
import { Section } from "../../shared/page";
import { runPath } from "../conversations/api";
import { MemoryRunLink } from "./run-link";
import { invalidateSpaces, spaceQueries } from "./spaces-api";
import styles from "./space-selection.module.css";

export function SpaceOrganization({ space }: { space: Schema["MemorySpace"] }) {
  const client = useClient(),
    cache = useQueryClient(),
    { workspace, basePath, can } = useWorkspace(),
    { t } = useTranslation();
  const queries = spaceQueries(client, workspace.id),
    page = useCursor(space.id);
  const status = useQuery(queries.status(space.id)),
    history = useQuery(queries.history(space.id, page.cursor));
  const organize = useMutation({
    mutationFn: () =>
      client
        .workspace(workspace.id)
        .POST("/api/v1/memory-spaces/{space_id}/organizations", {
          params: { path: { space_id: space.id } },
        })
        .then(data),
    onSuccess: () => {
      void invalidateSpaces(cache, workspace.id);
    },
  });
  const state = status.data;
  const ready =
    space.enabled &&
    !!space.organization.model &&
    !state?.active_run_id &&
    !state?.pending;
  return (
    <div className="grid gap-9">
      <Section
        title={t("Organization")}
        description={t(
          "Consolidate session notes into shared knowledge. Review every round and its file changes.",
        )}
        actions={
          can("run") && (
            <Button
              disabled={!ready || status.isPending || organize.isPending}
              onClick={() => organize.mutate()}
            >
              <ArrowsClockwiseIcon size={15} />
              {t("Organize now")}
            </Button>
          )
        }
      >
        <ErrorNotice error={status.error} retry={() => void status.refetch()} />
        <ErrorNotice error={organize.error} />
        {status.isPending ? (
          <Loading variant="form" />
        ) : (
          state && (
            <div className={styles.organization}>
              <div className={styles.heading}>
                <StatePill
                  state={
                    state.active_run_id
                      ? "running"
                      : state.pending
                        ? "pending"
                        : state.failure
                          ? "failed"
                          : "ready"
                  }
                />
                <span>
                  {t(
                    space.organization.enabled
                      ? "Automatic organization is on"
                      : "Manual organization",
                  )}
                </span>
              </div>
              <p>
                {!space.enabled
                  ? t("Enable this Space to organize its files.")
                  : !space.organization.model
                    ? t("Choose an organizer model in Configuration to begin.")
                    : state.active_run_id
                      ? t(
                          "An organization round is running. New changes remain queued for a later round.",
                        )
                      : state.pending
                        ? t("Organization is queued.")
                        : t("Ready to organize when you need it.")}
              </p>
              {state.history_gap && (
                <p role="status">
                  {t(
                    "Some revision history is no longer available. Automatic organization is paused; review the files and organize manually.",
                  )}
                </p>
              )}
              {state.failure && <p role="alert">{state.failure.message}</p>}
              {state.active_run_id && (
                <MemoryRunLink runId={state.active_run_id} explainUnavailable />
              )}
              {state.pending && state.due_at && (
                <p>
                  {t("Scheduled")}: <Timestamp value={state.due_at} />
                </p>
              )}
            </div>
          )
        )}
      </Section>
      <Section
        title={t("Organization history")}
        description={t(
          "Each round has its own thread in a dedicated organizer session.",
        )}
      >
        <ErrorNotice
          error={history.error}
          retry={() => void history.refetch()}
        />
        {history.isPending ? (
          <Loading variant="table" columns={3} />
        ) : history.data?.items.length ? (
          <>
            <ResourceTable
              caption={t("Organization history")}
              items={history.data.items}
              columns={[
                {
                  label: t("Run"),
                  tone: "primary",
                  render: (run) => (
                    <Link
                      to={runPath(
                        basePath,
                        { ...run, run_id: run.id },
                        "debug",
                      )}
                    >
                      {t("Organization round")}
                    </Link>
                  ),
                },
                {
                  label: t("Status"),
                  render: (run) => <StatePill state={run.status} />,
                },
                {
                  label: t("Created"),
                  tone: "muted",
                  render: (run) => <Timestamp value={run.created_at} />,
                },
              ]}
            />
            <CollectionFooter>
              <Pagination page={page} next={history.data.next_cursor} />
            </CollectionFooter>
          </>
        ) : (
          !history.error && (
            <Empty
              title={t("No organization rounds yet")}
              description={t(
                "Choose a model in Configuration, then organize manually or turn on automatic organization.",
              )}
            />
          )
        )}
      </Section>
    </div>
  );
}

export function SpaceSessions({ spaceId }: { spaceId: string }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation(),
    page = useCursor(spaceId);
  const query = useQuery(
    spaceQueries(client, workspace.id).sessions(spaceId, page.cursor),
  );
  return (
    <Section
      title={t("Associated sessions")}
      description={t(
        "Sessions that selected this Space, including those with memory participation turned off.",
      )}
    >
      <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      {query.isPending ? (
        <Loading variant="table" columns={3} />
      ) : query.data?.items.length ? (
        <>
          <ResourceTable
            caption={t("Associated sessions")}
            items={query.data.items}
            columns={[
              {
                label: t("Session"),
                tone: "primary",
                render: (session) => (
                  <Link
                    className={styles.sessionLink}
                    to={`${basePath}/sessions/${session.id}?view=chat`}
                  >
                    <span>
                      {session.preview?.input_text || t("Untitled session")}
                    </span>
                    <ArrowRightIcon size={14} aria-hidden="true" />
                  </Link>
                ),
              },
              {
                label: t("Memory participation"),
                render: (session) => (
                  <StatePill
                    state={session.memory?.enabled ? "enabled" : "disabled"}
                  />
                ),
              },
              {
                label: t("Updated"),
                tone: "muted",
                render: (session) => (
                  <Timestamp value={session.updated_at} relative />
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
            title={t("No associated sessions")}
            description={t(
              "Select this Space in a session or in an agent's defaults for new sessions.",
            )}
          />
        )
      )}
    </Section>
  );
}
