import { AguiContent, Button, DisclosureSection } from "a13n-ui";
import { Link } from "react-router";
import { useTranslation } from "react-i18next";
import type { Schema } from "../../../../shared/api";
import { MarkdownContent } from "../../../../shared/markdown";
import { formatCost } from "../../../../shared/cost";
import { Patch } from "../../../../shared/diff";
import { formatDuration, formatTokens, resultExcerpt } from "../../format";
import type {
  ActionEntry,
  ContentEntry,
  ModelEntry,
  OtherEntry,
  TimelineEntry,
} from "../../timeline";
import type { StepUsage } from "../../usage";
import { readQuestions } from "../questions";
import { workState } from "../entry-language";
import { PaneSection, PaneStats, PaneValue, RawDialog } from "./pane-parts";
import type { RunScope } from "./scope";
import styles from "./pane.module.css";

/** What one row holds, opened in place under it. */
export function EntryPane({
  entry,
  scope,
}: {
  entry: TimelineEntry;
  scope: RunScope;
}) {
  if (entry.kind === "model") return <ModelPane entry={entry} />;
  if (
    entry.kind === "reasoning" ||
    entry.kind === "guidance" ||
    entry.kind === "reply"
  )
    return <ContentPane entry={entry} />;
  if (entry.kind === "other") return <OtherPane entry={entry} />;
  if (!isAction(entry)) return null;
  return <ActionPane entry={entry} scope={scope} />;
}

/** Every entry that specializes a call carries the call's own arguments. */
function isAction(entry: TimelineEntry): entry is ActionEntry {
  return "arguments" in entry;
}

function ModelPane({ entry }: { entry: ModelEntry }) {
  const { t } = useTranslation();
  const usage = entry.usage;
  const reasoning = entry.children.filter(
    (child): child is ContentEntry => child.kind === "reasoning",
  );
  const calls = entry.children.filter(isAction);
  const replies = entry.children.filter(
    (child): child is ContentEntry => child.kind === "reply",
  );
  return (
    <div className={styles.pane}>
      <PaneStats
        facts={[
          // Tokens nobody reported are unknown: the fact is absent, not zero.
          usage &&
            entry.reportedUsage &&
            t("{{tokens}} in", { tokens: formatTokens(usage.inputTokens) }),
          usage &&
            entry.reportedUsage &&
            t("{{tokens}} out", { tokens: formatTokens(usage.outputTokens) }),
          cachedShare(usage) !== null &&
            t("{{percent}}% cached", { percent: cachedShare(usage) }),
          usage?.costUsd ? formatCost(usage.costUsd) : null,
          entry.durationMs !== null ? formatDuration(entry.durationMs) : null,
          entry.contextTokens
            ? t("{{tokens}} context", {
                tokens: formatTokens(entry.contextTokens),
              })
            : null,
          entry.stopReason &&
            t("stop {{reason}}", { reason: entry.stopReason }),
          entry.errorCode,
        ]}
        action={<RawDialog title={t("Model request")} value={entry} />}
      />
      <PaneSection label={t("Response")}>
        {reasoning.length || calls.length || replies.length ? (
          <div className={styles.responseBody}>
            {reasoning.map((child) => (
              <p key={child.id} className={styles.responseText}>
                {resultExcerpt(child.text, 400)}
              </p>
            ))}
            {calls.map((call) => (
              <p key={call.id} className={styles.responseCall}>
                <code>{call.name ?? t("Tool call")}</code>
                <span>{resultExcerpt(call.arguments, 160)}</span>
              </p>
            ))}
            {replies.map((child) => (
              <p key={child.id} className={styles.responseText}>
                {resultExcerpt(child.text, 320)}
              </p>
            ))}
          </div>
        ) : (
          <p className={styles.paneNote}>
            {t("This request produced no retained output.")}
          </p>
        )}
      </PaneSection>
      <DisclosureSection
        className={styles.paneDisclosure}
        title={
          <>
            {entry.messageCount === null
              ? t("Request")
              : t("Request · {{count}} messages", {
                  count: entry.messageCount,
                })}
          </>
        }
      >
        <p className={styles.paneNote}>
          {t(
            "The service retains observed execution, not the full prompt sent to the provider.",
          )}
        </p>
      </DisclosureSection>
    </div>
  );
}

/** Cache reads as a share of everything the request read, or null when unreported. */
function cachedShare(usage: StepUsage | null) {
  if (!usage?.cacheReadTokens) return null;
  const total = usage.inputTokens;
  return total ? Math.round((usage.cacheReadTokens / total) * 100) : null;
}

function ActionPane({ entry, scope }: { entry: ActionEntry; scope: RunScope }) {
  const { t } = useTranslation();
  const questions = entry.hitl
    ? readQuestions(entry.arguments as Schema["JsonValue"])
    : null;
  return (
    <div className={styles.pane}>
      <PaneStats
        facts={[
          entry.durationMs !== null ? formatDuration(entry.durationMs) : null,
          entry.outcome ??
            t(`state.${entry.state}`, { defaultValue: entry.state }),
          entry.callCount !== null
            ? t("{{count}} inner calls", { count: entry.callCount })
            : null,
          entry.providerUsage?.costUsd
            ? formatCost(entry.providerUsage.costUsd)
            : null,
          entry.waitingReason,
        ]}
        action={<RawDialog title={entry.name ?? t("Entry")} value={entry} />}
      />
      {entry.dispatchOnly && (
        <p className={styles.paneNote}>
          {t(
            "This row describes the dispatch. The child thread reports its own result.",
          )}
        </p>
      )}
      {scope.child && entry.kind === "subagent" && (
        <p className={styles.paneLinks}>
          <Link to={scope.child.path}>
            {t("Open {{name}} in full", { name: entry.name ?? t("subagent") })}
          </Link>
          <span>{t("{{count}} runs", { count: scope.child.runs })}</span>
        </p>
      )}
      {entry.hitl && (
        <PaneSection
          label={t(entry.hitl === "question" ? "Question" : "Approval")}
        >
          {questions ? (
            <div className={styles.questionSummary}>
              {questions.map((question, index) => (
                <div key={index}>
                  <strong>{question.header}</strong>
                  <p>{question.question}</p>
                  <p className={styles.paneNote}>
                    {question.options.map((option) => option.label).join(" · ")}
                  </p>
                </div>
              ))}
            </div>
          ) : (
            <PaneValue value={entry.arguments} />
          )}
          {workState(entry.state, scope.run.status) === "waiting" ? (
            scope.answerable ? (
              <Button
                size="sm"
                variant="outline"
                type="button"
                onClick={scope.jumpToDock}
              >
                {t("Answer below")}
              </Button>
            ) : (
              <p className={styles.paneNote}>
                {t("The owning application answers this request.")}
              </p>
            )
          ) : (
            <p className={styles.paneNote}>
              {resultExcerpt(entry.result, 200) || t("Resolved.")}
            </p>
          )}
        </PaneSection>
      )}
      {!entry.hitl && (
        <div className={styles.paneColumns}>
          <PaneSection label={t("Arguments")}>
            <PaneValue value={entry.arguments} />
          </PaneSection>
          <PaneSection label={t("Result")}>
            {entry.resultParts ? (
              <AguiContent parts={entry.resultParts} />
            ) : (
              <PaneValue value={entry.result} />
            )}
          </PaneSection>
        </div>
      )}
      {entry.edit && (
        <PaneSection
          label={t("Patch")}
          action={
            <code className={styles.panePath}>{entry.edit.filePath}</code>
          }
        >
          <Patch hunks={entry.edit.diff.hunks} />
        </PaneSection>
      )}
      {entry.failure != null && (
        <PaneSection label={t("Error details")}>
          <PaneValue value={entry.failure} />
        </PaneSection>
      )}
    </div>
  );
}

function ContentPane({ entry }: { entry: ContentEntry }) {
  const { t } = useTranslation();
  return (
    <div className={styles.pane}>
      <div className={styles.reasoningProse}>
        {entry.text ? (
          <MarkdownContent text={entry.text} />
        ) : (
          <p className={styles.paneNote}>{t("No text was retained.")}</p>
        )}
      </div>
      {entry.protectedReasoning && (
        <p className={styles.paneNote}>
          {t("The provider retained protected reasoning content.")}
        </p>
      )}
    </div>
  );
}

function OtherPane({ entry }: { entry: OtherEntry }) {
  const { t, i18n } = useTranslation();
  return (
    <div className={styles.pane}>
      <ul className={styles.observations}>
        {entry.observations.map((observation) => (
          <li key={observation.id}>
            <code>{observation.name}</code>
            {observation.occurredAt && (
              <span>
                {new Intl.DateTimeFormat(i18n.resolvedLanguage, {
                  timeStyle: "medium",
                }).format(new Date(observation.occurredAt))}
              </span>
            )}
            <RawDialog
              title={observation.name}
              value={observation.detail}
              label={t("Raw")}
            />
          </li>
        ))}
      </ul>
    </div>
  );
}
