import { Button, ChoiceField, FormField, Textarea } from "a13n-ui";
import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { useState } from "react";
import { useTranslation } from "react-i18next";
import { Link, useParams } from "react-router";
import { useClient } from "../../auth/context";
import { useWorkspace } from "../../layout/workspace";
import { data, ifMatch, rowTag, type Schema } from "../../shared/api";
import { MarkdownContent } from "../../shared/markdown";
import { ErrorNotice, Loading, Timestamp } from "../../shared/feedback";
import { Page, Section } from "../../shared/page";
import { useAgentComposer } from "../agents/composer";
import { assessmentLabels, Severity, useFindingAgents } from "./page";
import styles from "./findings.module.css";

export function FindingDetail() {
  const { findingId = "" } = useParams(),
    { t } = useTranslation(),
    client = useClient(),
    { workspace, basePath, can } = useWorkspace(),
    cache = useQueryClient(),
    composer = useAgentComposer(),
    agents = useFindingAgents();
  const query = useQuery({
    queryKey: ["finding", workspace.id, findingId],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/findings/{finding_id}", {
          params: { path: { finding_id: findingId } },
          signal,
        })
        .then(data),
  });
  const finding = query.data;
  const revision = useQuery({
    queryKey: ["agent-revision", workspace.id, finding?.agent_revision_id],
    enabled: !!finding,
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/agents/{agent_id}/revisions/{revision_id}", {
          params: {
            path: {
              agent_id: finding!.agent_id,
              revision_id: finding!.agent_revision_id,
            },
          },
          signal,
        })
        .then(data),
  });
  const update = useMutation({
    mutationFn: (body: Schema["FindingUpdate"]) =>
      client
        .workspace(workspace.id)
        .PATCH("/api/v1/findings/{finding_id}", {
          body,
          params: {
            path: { finding_id: findingId },
            header: ifMatch(rowTag(finding!)),
          },
        })
        .then(data),
    onSuccess: (value) => {
      cache.setQueryData(["finding", workspace.id, findingId], value);
      void cache.invalidateQueries({ queryKey: ["findings", workspace.id] });
    },
    onError: () => {
      void query.refetch();
    },
  });
  if (query.isPending) return <Loading page variant="detail" />;
  if (query.error || !finding)
    return (
      <Page title={t("Finding")} back={`${basePath}/findings`}>
        <ErrorNotice error={query.error} retry={() => void query.refetch()} />
      </Page>
    );
  const agent = agents.data?.find((agent) => agent.id === finding.agent_id);
  const fix = () => {
    if (!revision.data) return;
    composer.start({
      agent: { id: finding.agent_id, name: agent?.name ?? finding.agent_id },
      revision: { id: finding.agent_revision_id, number: revision.data.number },
      context: `Review finding ${finding.id} using read_finding before proposing a change. Verify the hypothesis and cited traces against the named revision, state evidence limitations, and preserve unrelated settings.`,
    });
  };
  return (
    <Page
      className={styles.detailPage}
      title={finding.title}
      back={`${basePath}/findings`}
      backLabel={t("Findings")}
    >
      {composer.setup}
      <ErrorNotice error={update.error ?? composer.error ?? revision.error} />
      <div className={styles.metadata}>
        <Severity finding={finding} />
        <span>{finding.closed ? t("Closed") : t("Open")}</span>
        <Link to={`${basePath}/agents/${finding.agent_id}`}>
          {agent?.name ?? finding.agent_id} ·{" "}
          {revision.data
            ? `v${revision.data.number}`
            : finding.agent_revision_id}
        </Link>
        <Timestamp value={finding.updated_at} relative />
      </div>
      <div className={styles.detail}>
        <div className={styles.narrative}>
          <Section title={t("What happened")}>
            <MarkdownContent text={finding.explanation} />
          </Section>
          <Section
            title={t("Evidence")}
            description={t(
              "Trace availability depends on backend retention and capture. External producers are responsible for their trace and span references.",
            )}
          >
            <div className={styles.evidenceList}>
              {finding.evidence.map((evidence, index) => (
                <Evidence key={index} evidence={evidence} number={index + 1} />
              ))}
            </div>
          </Section>
          <Section title={t("Suggested improvement")}>
            <MarkdownContent text={finding.suggestion} />
          </Section>
          {finding.limitations && (
            <Section title={t("Evidence limitations")}>
              <MarkdownContent text={finding.limitations} />
            </Section>
          )}
          <details className={styles.references}>
            <summary>{t("Technical references")}</summary>
            <dl className={styles.referenceList}>
              <dt>{t("Finding")}</dt>
              <dd>{finding.id}</dd>
              <dt>{t("Category")}</dt>
              <dd>{finding.category}</dd>
              <dt>{t("Agent version")}</dt>
              <dd>{finding.agent_revision_id}</dd>
              {finding.analysis_id && (
                <>
                  <dt>{t("Analysis")}</dt>
                  <dd>{finding.analysis_id}</dd>
                </>
              )}
            </dl>
            {finding.source_run_id && (
              <RunEvidence
                runId={finding.source_run_id}
                label={t("Open analysis run")}
              />
            )}
            {!finding.source_run_id && (
              <p className={styles.hint}>
                {t("Submitted through the findings API or an Agent tool.")}
              </p>
            )}
          </details>
        </div>
        <aside className={styles.reviewSidebar}>
          <Section
            title={t("Review")}
            description={t(
              "Severity describes impact. Assessment records your review of the evidence.",
            )}
          >
            <AssessmentEditor
              key={`${finding.id}:${finding.version}`}
              finding={finding}
              canWrite={can("write")}
              pending={update.isPending}
              onSave={(body) => update.mutate(body)}
            />
          </Section>
          <Section
            title={t("Next step")}
            description={t(
              "Composer opens with this evidence and the cited version. Review the proposed change before approving it.",
            )}
          >
            {can("run") && composer.available && (
              <Button
                disabled={
                  !revision.data ||
                  composer.pending ||
                  ["expected", "false_positive"].includes(finding.assessment)
                }
                onClick={fix}
              >
                {t("Review with Composer")}
              </Button>
            )}
            {can("write") && (
              <Button
                variant="outline"
                disabled={update.isPending}
                onClick={() => update.mutate({ closed: !finding.closed })}
              >
                {finding.closed ? t("Reopen finding") : t("Close finding")}
              </Button>
            )}
          </Section>
        </aside>
      </div>
    </Page>
  );
}
function Evidence({
  evidence,
  number,
}: {
  evidence: Schema["Evidence"];
  number: number;
}) {
  const { basePath } = useWorkspace(),
    { t } = useTranslation();
  return (
    <div className={styles.evidenceCard}>
      <div className={styles.evidenceActions}>
        <Link
          className={styles.link}
          to={`${basePath}/traces/${evidence.trace_id}`}
          title={evidence.trace_id}
        >
          {t("Trace")} {number}
        </Link>
        <RunEvidence runId={evidence.run_id} />
      </div>
      <details className={styles.references}>
        <summary>{t("Trace references")}</summary>
        <dl className={styles.referenceList}>
          <dt>{t("Trace")}</dt>
          <dd>{evidence.trace_id}</dd>
          <dt>{t("Run")}</dt>
          <dd>{evidence.run_id}</dd>
          {evidence.span_ids?.length ? (
            <>
              <dt>{t("Steps")}</dt>
              <dd>{evidence.span_ids.join(", ")}</dd>
            </>
          ) : null}
        </dl>
      </details>
    </div>
  );
}
function RunEvidence({ runId, label }: { runId: string; label?: string }) {
  const client = useClient(),
    { workspace, basePath } = useWorkspace(),
    { t } = useTranslation();
  const query = useQuery({
    queryKey: ["run", workspace.id, runId],
    queryFn: ({ signal }) =>
      client
        .workspace(workspace.id)
        .GET("/api/v1/runs/{run_id}", {
          params: { path: { run_id: runId } },
          signal,
        })
        .then(data),
  });
  return (
    <>
      <ErrorNotice error={query.error} />
      {query.data ? (
        <Link
          className={styles.link}
          title={runId}
          to={`${basePath}/sessions/${query.data.session_id}/threads/${query.data.thread_id}/runs/${runId}`}
        >
          {label ?? t("Open run")}
        </Link>
      ) : (
        <span className={styles.hint}>{t("Run")}</span>
      )}
    </>
  );
}

function AssessmentEditor({
  finding,
  canWrite,
  pending,
  onSave,
}: {
  finding: Schema["Finding"];
  canWrite: boolean;
  pending: boolean;
  onSave: (body: Schema["FindingUpdate"]) => void;
}) {
  const { t } = useTranslation();
  const [assessment, setAssessment] = useState(finding.assessment);
  const [note, setNote] = useState(finding.assessment_note);
  const [editing, setEditing] = useState(false);
  const dirty =
    assessment !== finding.assessment || note !== finding.assessment_note;
  if (!editing)
    return (
      <div className={styles.reviewSummary}>
        <strong>{t(assessmentLabels[finding.assessment])}</strong>
        {finding.assessment_note ? (
          <MarkdownContent text={finding.assessment_note} />
        ) : (
          <p className={styles.hint}>{t("No review note yet.")}</p>
        )}
        {canWrite && (
          <Button
            variant="outline"
            disabled={pending}
            onClick={() => setEditing(true)}
          >
            {t("Edit assessment")}
          </Button>
        )}
      </div>
    );
  return (
    <form
      className={styles.form}
      onSubmit={(event) => {
        event.preventDefault();
        if (canWrite && dirty && !pending)
          onSave({ assessment, assessment_note: note });
      }}
    >
      <ChoiceField
        label={t("Assessment")}
        value={assessment}
        readOnly={!canWrite}
        disabled={pending}
        options={Object.entries(assessmentLabels).map(([value, label]) => ({
          value,
          label: t(label),
        }))}
        onValueChange={(value) => {
          if (value !== assessment) setNote("");
          setAssessment(value as Schema["Finding"]["assessment"]);
        }}
      />
      <FormField
        label={t("Review note")}
        description={t(
          "Explain your judgment. Later analyses of this Agent version will receive reviewed findings and their notes.",
        )}
      >
        <Textarea
          rows={3}
          maxLength={2048}
          value={note}
          readOnly={!canWrite}
          disabled={pending}
          placeholder={t("What did the analysis miss or misunderstand?")}
          onChange={(event) => setNote(event.target.value)}
        />
      </FormField>
      {canWrite && (
        <div className={styles.actions}>
          <Button
            type="button"
            variant="outline"
            disabled={pending}
            onClick={() => {
              setAssessment(finding.assessment);
              setNote(finding.assessment_note);
              setEditing(false);
            }}
          >
            {t("Cancel")}
          </Button>
          <Button type="submit" disabled={!dirty || pending} loading={pending}>
            {t("Save assessment")}
          </Button>
        </div>
      )}
    </form>
  );
}
