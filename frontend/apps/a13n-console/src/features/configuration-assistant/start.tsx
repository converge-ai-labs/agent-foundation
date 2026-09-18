import {
  ArrowRightIcon,
  ArrowUpRightIcon,
  ChatCircleTextIcon,
  CheckCircleIcon,
  SlidersHorizontalIcon,
  SparkleIcon,
  CubeIcon,
  LockSimpleIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useRef } from "react";
import { Composer } from "../conversations/composer";
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
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Page } from "../../shared/page";
import { Pagination, useCursor } from "../../shared/collection";
import { useIdempotency } from "../../shared/idempotency";
import { useAssistantReadiness } from "./api";
import styles from "./configuration.module.css";

export function ReadinessNotice({
  readiness,
  prerequisite = false,
}: {
  readiness: Schema["AssistantReadiness"];
  prerequisite?: boolean;
}) {
  const { t } = useTranslation();
  if (readiness.ready) return null;
  const messages = {
    ready: "Ready",
    provider_setup_required:
      "Configure a model provider to start the assistant.",
    model_setup_required:
      "Provider configured. Add an enabled model to continue.",
    compatible_model_required: "Choose a model that supports tool calling.",
    model_access_denied: "Ask an administrator for model access.",
  };
  const needsModel =
    readiness.reason_code === "model_setup_required" ||
    readiness.reason_code === "compatible_model_required";
  return (
    <div
      role="status"
      className={prerequisite ? styles.prerequisite : styles.notice}
    >
      {prerequisite ? (
        <span className={styles.stepNumber} aria-hidden="true">
          1
        </span>
      ) : (
        <CubeIcon size={22} aria-hidden="true" />
      )}
      <div className={styles.noticeBody}>
        {prerequisite && (
          <div className={styles.setupHeading}>
            <h3>
              {t(
                readiness.reason_code === "model_setup_required"
                  ? "Provider ready. Add a model next"
                  : "Set up a model first",
              )}
            </h3>
            <span>{t("Required")}</span>
          </div>
        )}
        <p>{t(messages[readiness.reason_code])}</p>
        {readiness.setup_actions.includes("contact_administrator") ? (
          <p>{t("Contact your administrator to complete setup.")}</p>
        ) : prerequisite ? (
          <>
            <Button
              render={
                <Link
                  to={readiness.setup_url}
                  target="_blank"
                  rel="noreferrer"
                />
              }
            >
              <CubeIcon size={16} aria-hidden="true" />
              {t(needsModel ? "Open Models" : "Open model setup")}
              <ArrowUpRightIcon size={15} aria-hidden="true" />
            </Button>
            <p>
              {t(
                "Complete model setup in the new tab, then return here to continue.",
              )}
            </p>
          </>
        ) : (
          <Link to={readiness.setup_url} target="_blank" rel="noreferrer">
            {t(needsModel ? "Open Models" : "Open model setup")}
            <ArrowUpRightIcon size={15} aria-hidden="true" />
          </Link>
        )}
      </div>
    </div>
  );
}

export function ConfigurationStart() {
  const { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath, can } = useWorkspace();
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
  const created = useRef<{
    scope: string;
    session: Schema["ConfigurationSessionView"];
  } | null>(null);
  const scope = JSON.stringify([workspace.id, target, revision]);
  async function start(input: Schema["AgentInput"], inputKey: string) {
    if (created.current?.scope !== scope) {
      const body: Schema["CreateSessionRequest"] = {
        target_agent_id: target,
        source: revision
          ? { selector: "explicit", revision_id: revision }
          : undefined,
      };
      const session = data(
        await client.http.POST(
          "/api/v1/workspaces/{workspace}/configuration-sessions",
          {
            params: {
              path: { workspace: workspace.id },
              header: commandHeaders(workspace.id, key.forBody(body)),
            },
            body,
          },
        ),
      );
      // Retain the created Session if first-input admission fails, so retry cannot create another draft.
      created.current = { scope, session };
    }
    const receipt = data(
      await client.http.POST(
        "/api/v1/configuration-threads/{thread_id}/inputs",
        {
          params: {
            path: { thread_id: created.current.session.root_thread_id },
            header: commandHeaders(workspace.id, inputKey),
          },
          body: { input, expected_thread_version: 1 },
        },
      ),
    );
    navigate(
      `${basePath}/configuration-threads/${receipt.thread_id}?run=${receipt.run_id}`,
    );
  }
  return (
    <Page title={t("Configuration assistant")}>
      <div className={styles.start}>
        <section className={styles.intro}>
          <div className={styles.assistantIcon}>
            <SparkleIcon size={28} weight="duotone" aria-hidden="true" />
          </div>
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
          <ol className={styles.steps}>
            {[
              {
                icon: ChatCircleTextIcon,
                title: "Describe your goal",
                description: "Tell the assistant what your agent should do.",
              },
              {
                icon: SlidersHorizontalIcon,
                title: "Shape the details",
                description: "Refine instructions, models and tools together.",
              },
              {
                icon: CheckCircleIcon,
                title: "Review and apply",
                description: "You decide when the draft becomes your agent.",
              },
            ].map(({ icon: Icon, title, description }) => (
              <li key={title}>
                <Icon size={20} aria-hidden="true" />
                <h3>{t(title)}</h3>
                <p>{t(description)}</p>
              </li>
            ))}
          </ol>
          {revision && (
            <p>
              {t("Source revision")}: <code>{revision}</code>
            </p>
          )}
          <div className={styles.startActions}>
            {readiness.isPending && <Loading />}
            <ErrorNotice
              error={readiness.error}
              retry={() => void readiness.refetch()}
            />
            {readiness.data && !readiness.data.ready ? (
              <div className={styles.setupFlow}>
                <ReadinessNotice readiness={readiness.data} prerequisite />
                <div className={styles.lockedStep}>
                  <span className={styles.stepNumber} aria-hidden="true">
                    2
                  </span>
                  <div>
                    <h3>
                      <LockSimpleIcon size={16} aria-hidden="true" />
                      {t("Start configuration conversation")}
                    </h3>
                    <p>{t("Available once a compatible model is ready.")}</p>
                  </div>
                </div>
              </div>
            ) : (
              <div className={styles.startComposer}>
                <Composer
                  key={scope}
                  disabled={!readiness.data?.ready || !can("run.continue")}
                  label={t("Start configuration conversation")}
                  submit={start}
                />
                <p className={styles.startHint}>
                  {t(
                    "A conversation is created when you send your first message.",
                  )}
                </p>
              </div>
            )}
          </div>
        </section>
        <section className={styles.history}>
          <h2>{t("Your configuration conversations")}</h2>
          <ErrorNotice
            error={sessions.error}
            retry={() => void sessions.refetch()}
          />
          {sessions.isPending && <Loading />}
          {sessions.data?.items.length === 0 && (
            <div className={styles.historyEmpty}>
              <ChatCircleTextIcon size={24} aria-hidden="true" />
              <div>
                <h3>{t("No configuration conversations yet")}</h3>
                <p>
                  {t(
                    "Your conversations will appear here so you can return to a draft anytime.",
                  )}
                </p>
              </div>
            </div>
          )}
          {sessions.data?.items.map((session) => (
            <Link
              key={session.id}
              to={`${basePath}/configuration-threads/${session.root_thread_id}`}
            >
              <ChatCircleTextIcon size={20} aria-hidden="true" />
              <span className={styles.sessionIdentity}>
                <span
                  className={styles.sessionTitle}
                  title={session.title ?? undefined}
                >
                  {session.title || t("Configuration draft")}
                </span>
                <span className={styles.draftId}>
                  {t(
                    session.has_runs === false
                      ? "Not started"
                      : "Configuration draft",
                  )}
                </span>
              </span>
              <Timestamp value={session.updated_at} relative />
              <ArrowRightIcon size={16} aria-hidden="true" />
            </Link>
          ))}
          <Pagination page={page} next={sessions.data?.next_cursor} />
        </section>
      </div>
    </Page>
  );
}
