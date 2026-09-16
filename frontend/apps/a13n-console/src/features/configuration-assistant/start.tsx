import { useMutation, useQuery } from "@tanstack/react-query";
import { Button } from "a13n-ui";
import { Link, useNavigate, useSearchParams } from "react-router";
import { useTranslation } from "react-i18next";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import {
  commandHeaders,
  data,
  workspaceHeaders,
  type Schema,
} from "../../shared/api";
import { ErrorNotice, Loading, Page, Timestamp } from "../../shared/feedback";
import { Pagination, useCursor } from "../../shared/collection";
import { useIdempotency } from "../../shared/idempotency";
import { useAssistantReadiness } from "./api";
import styles from "./configuration.module.css";

export function ReadinessNotice({
  readiness,
}: {
  readiness: Schema["AssistantReadiness"];
}) {
  const { t } = useTranslation();
  if (readiness.ready) return null;
  const messages = {
    ready: "Ready",
    provider_setup_required:
      "Configure a model provider to start the assistant.",
    model_setup_required: "Add an enabled model to start the assistant.",
    compatible_model_required: "Choose a model that supports tool calling.",
    model_access_denied: "Ask an administrator for model access.",
  };
  return (
    <div role="status" className={styles.notice}>
      <p>{t(messages[readiness.reason_code])}</p>
      {readiness.setup_actions.includes("contact_administrator") ? (
        <p>{t("Contact your administrator to complete setup.")}</p>
      ) : (
        <Link to={readiness.setup_url} target="_blank" rel="noreferrer">
          {t("Open model setup")}
        </Link>
      )}
    </div>
  );
}

export function ConfigurationStart() {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath } = useWorkspace();
  const [params] = useSearchParams(),
    navigate = useNavigate(),
    key = useIdempotency(),
    page = useCursor();
  const target = params.get("agent"),
    revision = params.get("revision");
  const readiness = useAssistantReadiness(target);
  const sessions = useQuery({
    queryKey: ["configuration-sessions", workspace.id, page.cursor],
    queryFn: ({ signal }) =>
      client.http
        .GET("/api/v1/workspaces/{workspace}/configuration-sessions", {
          params: {
            path: { workspace: workspace.id },
            query: { limit: 20, cursor: page.cursor },
          },
          headers: workspaceHeaders(workspace.id),
          signal,
        })
        .then(data),
  });
  const create = useMutation({
    mutationFn: () => {
      const body: Schema["CreateSessionRequest"] = {
        target_agent_id: target,
        source: revision
          ? { selector: "explicit", revision_id: revision }
          : undefined,
      };
      return client.http
        .POST("/api/v1/workspaces/{workspace}/configuration-sessions", {
          params: {
            path: { workspace: workspace.id },
            header: commandHeaders(workspace.id, key.forBody(body)),
          },
          body,
        })
        .then(data);
    },
    onSuccess: (session) => {
      key.reset();
      navigate(`${basePath}/configuration-threads/${session.root_thread_id}`);
    },
  });
  return (
    <Page title={t("Configuration assistant")}>
      <div className={styles.start}>
        <section className={styles.intro}>
          <h2>
            {t(
              target
                ? "Improve an agent together"
                : "Describe the agent you want to build",
            )}
          </h2>
          <p>
            {t(
              "Discuss instructions, models and tools. Review the draft before applying it to your agent.",
            )}
          </p>
          {revision && (
            <p>
              {t("Source revision")}: <code>{revision}</code>
            </p>
          )}
          {readiness.isPending && <Loading />}
          <ErrorNotice
            error={readiness.error ?? create.error}
            retry={() => void readiness.refetch()}
          />
          {readiness.data && <ReadinessNotice readiness={readiness.data} />}
          <Button
            disabled={!readiness.data?.ready}
            loading={create.isPending}
            onClick={() => create.mutate()}
          >
            {t("Start configuration conversation")}
          </Button>
        </section>
        <section className={styles.history}>
          <h2>{t("Your configuration conversations")}</h2>
          <ErrorNotice
            error={sessions.error}
            retry={() => void sessions.refetch()}
          />
          {sessions.data?.items
            .filter((session) => !target || session.target_agent_id === target)
            .map((session) => (
              <Link
                key={session.id}
                to={`${basePath}/configuration-threads/${session.root_thread_id}`}
              >
                <span>
                  {t(session.target_agent_id ? "Update agent" : "Create agent")}
                </span>
                <Timestamp value={session.updated_at} relative />
              </Link>
            ))}
          <Pagination page={page} next={sessions.data?.next_cursor} />
        </section>
      </div>
    </Page>
  );
}
