import {
  ChatCircleTextIcon,
  CheckCircleIcon,
  CubeIcon,
  LockSimpleIcon,
  SlidersHorizontalIcon,
  SparkleIcon,
} from "@phosphor-icons/react";
import { useQuery } from "@tanstack/react-query";
import { useRef } from "react";
import { Composer } from "../conversations/composer";
import { Button, StatusPill } from "a13n-ui";
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
import {
  CollectionFooter,
  Empty,
  ListRow,
  ListRows,
  Pagination,
  useCursor,
} from "../../shared/collection";
import { useIdempotency } from "../../shared/idempotency";
import { useAssistantReadiness } from "./api";
import styles from "./configuration.module.css";

const readinessMessages: Record<string, string> = {
  ready: "Ready",
  provider_setup_required: "Configure a model provider to start the assistant.",
  model_setup_required:
    "Provider configured. Add an enabled model to continue.",
  compatible_model_required: "Choose a model that supports tool calling.",
  model_access_denied: "Ask an administrator for model access.",
};

/** What is missing before the assistant can run, and the way to fix it. */
export function ReadinessNotice({
  readiness,
  prerequisite = false,
}: {
  readiness: Schema["AssistantReadiness"];
  prerequisite?: boolean;
}) {
  const { t } = useTranslation();
  if (readiness.ready) return null;
  const needsModel =
    readiness.reason_code === "model_setup_required" ||
    readiness.reason_code === "compatible_model_required";
  const openLabel = t(needsModel ? "Open Models" : "Open model setup");
  const cannotAct = readiness.setup_actions.includes("contact_administrator");
  if (!prerequisite)
    return (
      <p role="status" className={styles.notice}>
        <CubeIcon size={16} aria-hidden="true" />
        <span>
          {t(readinessMessages[readiness.reason_code])}{" "}
          {cannotAct ? (
            t("Contact your administrator to complete setup.")
          ) : (
            <Link to={readiness.setup_url}>{openLabel}</Link>
          )}
        </span>
      </p>
    );
  return (
    <div role="status" className={styles.step} data-state="required">
      <span className={styles.stepNumber} aria-hidden="true">
        1
      </span>
      <div className={styles.stepBody}>
        <div className={styles.stepHeading}>
          <h3>
            {t(
              readiness.reason_code === "model_setup_required"
                ? "Provider ready. Add a model next"
                : "Set up a model first",
            )}
          </h3>
          <StatusPill variant="warning">{t("Required")}</StatusPill>
        </div>
        <p>{t(readinessMessages[readiness.reason_code])}</p>
        {cannotAct ? (
          <p>{t("Contact your administrator to complete setup.")}</p>
        ) : (
          <Button size="sm" render={<Link to={readiness.setup_url} />}>
            <CubeIcon size={15} aria-hidden="true" />
            {openLabel}
          </Button>
        )}
      </div>
    </div>
  );
}

const howItWorks = [
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
];

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
  const ready = readiness.data?.ready;
  return (
    <div className={styles.start}>
      <header className={styles.intro}>
        <span className={styles.assistantIcon}>
          <SparkleIcon size={24} weight="duotone" aria-hidden="true" />
        </span>
        <h1>
          {t(
            target
              ? "Improve an agent together"
              : "Describe the agent you want to build",
          )}
        </h1>
        <p>
          {t(
            "Discuss instructions, models and tools. Review the draft before applying it to your agent.",
          )}
        </p>
      </header>
      <ol className={styles.steps}>
        {howItWorks.map(({ icon: Icon, title, description }) => (
          <li key={title}>
            <Icon size={18} aria-hidden="true" />
            <h2>{t(title)}</h2>
            <p>{t(description)}</p>
          </li>
        ))}
      </ol>
      {revision && (
        <p className={styles.sourceRevision}>
          {t("Source revision")} <code>{revision}</code>
        </p>
      )}
      <div className={styles.startActions}>
        <ErrorNotice
          error={readiness.error}
          retry={() => void readiness.refetch()}
        />
        {readiness.data && !ready ? (
          <div className={styles.setupFlow}>
            <ReadinessNotice readiness={readiness.data} prerequisite />
            <div className={styles.step} data-state="locked">
              <span className={styles.stepNumber} aria-hidden="true">
                2
              </span>
              <div className={styles.stepBody}>
                <div className={styles.stepHeading}>
                  <h3>
                    <LockSimpleIcon size={15} aria-hidden="true" />
                    {t("Start configuration conversation")}
                  </h3>
                </div>
                <p>{t("Available once a compatible model is ready.")}</p>
              </div>
            </div>
          </div>
        ) : (
          <div className={styles.startComposer}>
            <Composer
              key={scope}
              disabled={!ready || !can("run.continue")}
              label={t("Start configuration conversation")}
              submit={start}
            />
            <p className={styles.startHint}>
              {t("A conversation is created when you send your first message.")}
            </p>
          </div>
        )}
      </div>
      <section className={styles.history}>
        <h2>{t("Your configuration conversations")}</h2>
        <ErrorNotice
          error={sessions.error}
          retry={() => void sessions.refetch()}
        />
        {sessions.isPending && <Loading variant="list" />}
        {sessions.data?.items.length === 0 && (
          <Empty
            icon={<ChatCircleTextIcon aria-hidden="true" />}
            title={t("No configuration conversations yet")}
            description={t(
              "Your conversations will appear here so you can return to a draft anytime.",
            )}
          />
        )}
        {!!sessions.data?.items.length && (
          <>
            <ListRows>
              {sessions.data.items.map((session) => (
                <Link
                  key={session.id}
                  className={styles.sessionRow}
                  to={`${basePath}/configuration-threads/${session.root_thread_id}`}
                >
                  <ListRow
                    icon={<ChatCircleTextIcon size={16} aria-hidden="true" />}
                    name={session.title || t("Configuration draft")}
                    secondary={
                      <Timestamp value={session.updated_at} relative />
                    }
                    control={
                      <StatusPill
                        variant={
                          session.has_runs === false ? "neutral" : "info"
                        }
                      >
                        {t(
                          session.has_runs === false ? "Not started" : "Draft",
                        )}
                      </StatusPill>
                    }
                  />
                </Link>
              ))}
            </ListRows>
            <CollectionFooter>
              <Pagination page={page} next={sessions.data.next_cursor} />
            </CollectionFooter>
          </>
        )}
      </section>
    </div>
  );
}
